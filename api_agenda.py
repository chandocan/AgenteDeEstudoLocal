#!/usr/bin/env python3
"""API REST da agenda de estudos — etapa 2 (comunicação servidor ↔ agente).

O agente local (OpenCode + Ollama) consome esta API para:
  1. listar/reivindicar tarefas de geração (estudos com data chegada);
  2. devolver o conteúdo da aula produzido;
  3. reportar falhas de geração.

Escuta apenas em 127.0.0.1 por padrão. Variáveis de ambiente:
  AGENDA_DB_* (compartilhadas com agenda_estudo.py)
  AGENDA_API_HOST (padrão 127.0.0.1)
  AGENDA_API_PORT (padrão 8800)
  AGENDA_API_TOKEN (opcional; se definido, exige cabeçalho X-API-Token)

Estado de geração do estudo:
  aguardando -> gerando -> em_producao -> gerado
                                     `-> (falha volta para gerando)
"""
import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field
import uvicorn

from agenda_estudo import (
    DB,
    agora,
    conectar,
    criar_tabelas,
    historico,
    logar,
    notificacao,
)

HOST = os.getenv("AGENDA_API_HOST", "127.0.0.1")
PORT = int(os.getenv("AGENDA_API_PORT", "8800"))
TOKEN = os.getenv("AGENDA_API_TOKEN", "")

app = FastAPI(title="Agenda de Estudos", version="1.0.0")


def exigir_token(x_api_token: Optional[str] = Header(default=None)):
    if not TOKEN:
        return
    if x_api_token != TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido ou ausente.")


# ---------------------------------------------------------------- modelos


class Reivindicacao(BaseModel):
    agente: str = Field(default="agente_local", max_length=80)
    quantidade: int = Field(default=1, ge=1, le=10)


class ConteudoEntrada(BaseModel):
    conteudo: str = Field(min_length=1)
    gerado_por: str = Field(default="agente_local", max_length=80)


class FalhaEntrada(BaseModel):
    motivo: str = Field(default="", max_length=2000)


# ---------------------------------------------------------------- endpoints


@app.get("/api/saude")
def saude(_=Depends(exigir_token)):
    conn = conectar()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
    finally:
        conn.close()
    return {"status": "ok", "banco": DB["database"]}


@app.get("/api/tarefas/pendentes")
def tarefas_pendentes(_=Depends(exigir_token)):
    """Estudos com data de geração chegada e ainda não reivindicados."""
    conn = conectar()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT id, tema, topicos, gerar_em, prazo_em
               FROM estudos WHERE status_geracao='gerando' ORDER BY gerar_em"""
        )
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    return {"pendentes": rows}


@app.get("/api/estudos")
def listar_estudos(
    status: Optional[str] = Query(default=None), _=Depends(exigir_token)
):
    conn = conectar()
    try:
        cur = conn.cursor(dictionary=True)
        if status:
            cur.execute(
                "SELECT * FROM estudos WHERE status_geracao=%s ORDER BY gerar_em",
                (status,),
            )
        else:
            cur.execute("SELECT * FROM estudos ORDER BY gerar_em")
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    return {"estudos": rows}


@app.post("/api/tarefas/reivindicar")
def reivindicar(req: Reivindicacao, _=Depends(exigir_token)):
    """Reivindica tarefas 'gerando': muda status para 'em_producao'.

   usa FOR UPDATE SKIP LOCKED para evitar disputa entre dois agentes.
    """
    conn = conectar()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT id, tema, topicos, gerar_em, prazo_em
               FROM estudos WHERE status_geracao='gerando'
               ORDER BY gerar_em LIMIT %s FOR UPDATE SKIP LOCKED""",
            (req.quantidade,),
        )
        escolhidos = cur.fetchall()
        for r in escolhidos:
            upd = conn.cursor()
            upd.execute(
                "UPDATE estudos SET status_geracao='em_producao' WHERE id=%s",
                (r["id"],),
            )
            upd.close()
            historico(
                conn,
                r["id"],
                "geracao_reivindicada",
                f"Tarefa reivindicada pelo agente '{req.agente}'.",
            )
            logar(
                f"TAREFA REIVINDICADA: estudo #{r['id']} — {r['tema']} "
                f"por '{req.agente}'"
            )
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"tarefas": escolhidos, "agente": req.agente}


@app.post("/api/tarefas/{estudo_id}/conteudo")
def receber_conteudo(
    estudo_id: int, dados: ConteudoEntrada, _=Depends(exigir_token)
):
    """Recebe o conteúdo gerado e marca o estudo como 'gerado'."""
    conn = conectar()
    try:
        conn.start_transaction()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id, tema, status_geracao FROM estudos WHERE id=%s FOR UPDATE",
            (estudo_id,),
        )
        estudo = cur.fetchone()
        if not estudo:
            raise HTTPException(status_code=404, detail=f"Estudo {estudo_id} não existe.")
        if estudo["status_geracao"] not in ("gerando", "em_producao"):
            raise HTTPException(
                status_code=409,
                detail=f"Estudo {estudo_id} está em '{estudo['status_geracao']}'; "
                       "conteúdo só é aceito em 'gerando' ou 'em_producao'.",
            )
        cur.close()

        cur = conn.cursor()
        cur.execute(
            """INSERT INTO conteudos
               (estudo_id,conteudo,gerado_por,status,criado_em)
               VALUES (%s,%s,%s,'recebido',%s)""",
            (estudo_id, dados.conteudo, dados.gerado_por, agora()),
        )
        conteudo_id = cur.lastrowid
        cur.execute(
            "UPDATE estudos SET status_geracao='gerado' WHERE id=%s",
            (estudo_id,),
        )
        cur.close()
        notificacao(
            conn,
            estudo_id,
            "conteudo_disponivel",
            (f"Conteúdo da aula #{estudo_id} — {estudo['tema']} está pronto "
             f"e disponível no banco (referência #{conteudo_id})."),
        )
        historico(
            conn,
            estudo_id,
            "conteudo_recebido",
            (f"Conteúdo recebido de '{dados.gerado_por}' "
             f"({len(dados.conteudo)} caracteres), referência #{conteudo_id}."),
        )
        logar(f"CONTEUDO RECEBIDO: estudo #{estudo_id} — {estudo['tema']}")
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"estudo_id": estudo_id, "conteudo_id": conteudo_id,
            "status_geracao": "gerado"}


@app.post("/api/tarefas/{estudo_id}/falha")
def falha_geracao(
    estudo_id: int, dados: FalhaEntrada, _=Depends(exigir_token)
):
    """Devolve a tarefa para 'gerando' para nova tentativa mais tarde."""
    conn = conectar()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id, tema, status_geracao FROM estudos WHERE id=%s FOR UPDATE",
            (estudo_id,),
        )
        estudo = cur.fetchone()
        cur.close()
        if not estudo:
            raise HTTPException(status_code=404, detail=f"Estudo {estudo_id} não existe.")
        if estudo["status_geracao"] not in ("gerando", "em_producao"):
            raise HTTPException(
                status_code=409,
                detail=f"Estudo {estudo_id} está em '{estudo['status_geracao']}'.",
            )
        upd = conn.cursor()
        upd.execute(
            "UPDATE estudos SET status_geracao='gerando' WHERE id=%s",
            (estudo_id,),
        )
        upd.close()
        historico(
            conn,
            estudo_id,
            "geracao_falhou",
            f"Falha reportada pelo agente: {dados.motivo or '(sem detalhe)'}. "
            "Estudo devolvido para 'gerando'.",
        )
        logar(f"FALHA DE GERACAO: estudo #{estudo_id} — devolvido para 'gerando'")
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"estudo_id": estudo_id, "status_geracao": "gerando"}


# ---------------------------------------------------------------- main


if __name__ == "__main__":
    criar_tabelas()
    logar(f"API iniciada em http://{HOST}:{PORT} (banco {DB['host']}/{DB['database']})")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
