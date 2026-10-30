#!/usr/bin/env python3
"""Trabalhador do agente de estudo — etapa 3 (OpenCode/Ollama).

Ciclo: lista tarefas pendentes na API -> reivindica -> gera a aula no
Ollama local -> entrega via API. Em erro, reporta falha (a tarefa volta
a disputar em 'gerando').

Na inicialização, devolve para 'gerando' qualquer estudo preso em
'em_producao' (assumindo que o trabalhador anterior morreu; projeto
considera um único trabalhador).

Variáveis de ambiente:
  AGENDA_DB_*            (compartilhadas com agenda_estudo.py, para logs)
  AGENDA_API_URL         (padrão http://127.0.0.1:8800)
  AGENDA_API_TOKEN       (se a API exigir)
  AGENDA_MODELO          (padrão qwen3.5:4b)
  AGENDA_OLLAMA_URL      (padrão http://127.0.0.1:11434)
  AGENDA_AGENTE_INTERVALO (segundos entre varreduras; padrão 30)
  AGENDA_AGENTE_TIMEOUT   (segundos de timeout da geração; padrão 600)

Execução manual one-shot (para testes): --uma-vez processa o que
houver pendente e encerra.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

from cliente_agente_teste import chamar

MODELO = os.getenv("AGENDA_MODELO", "qwen3.5:4b")
OLLAMA_URL = os.getenv("AGENDA_OLLAMA_URL", "http://127.0.0.1:11434")
INTERVALO = int(os.getenv("AGENDA_AGENTE_INTERVALO", "30"))
TIMEOUT = int(os.getenv("AGENDA_AGENTE_TIMEOUT", "600"))
AGENTE = os.getenv("AGENDA_AGENTE_NOME", "agente-local")

PROMPT_SISTEMA = (
    "Você é um professor. Escreva o conteúdo de uma aula em português do "
    "Brasil, em formato Markdown, didático e objetivo, para um estudante.")


def montar_prompt(tarefa):
    topicos = tarefa.get("topicos") or "não informados"
    return (
        "Crie o conteúdo de estudo abaixo.\n\n"
        f"Tema: {tarefa['tema']}\n"
        f"Tópicos que devem ser abordados: {topicos}\n"
        f"Prazo do estudante para concluir: {tarefa['prazo_em']}\n\n"
        "Estruture o texto em Markdown com: título, introdução, "
        "desenvolvimento de cada tópico com exemplos, resumo/revisão e "
        "de 3 a 5 perguntas para autoavaliar a revisão. "
        "Escreva apenas a aula, sem comentários sobre o processo.")


def chamar_ollama(mensagens):
    corpo = {
        "model": MODELO,
        "messages": mensagens,
        "stream": False,
        "think": False,
    }
    dados = json.dumps(corpo).encode("utf-8")
    requisicao = urllib.request.Request(
        OLLAMA_URL + "/api/chat", data=dados, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(requisicao, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))["message"]["content"]
    except urllib.error.HTTPError as exc:
        detalhe = exc.read().decode("utf-8", "replace")
        # Modelos sem suporte a "think": tenta novamente sem o parâmetro.
        if exc.code == 400 and '"think"' in detalhe:
            corpo.pop("think")
            dados = json.dumps(corpo).encode("utf-8")
            requisicao = urllib.request.Request(
                OLLAMA_URL + "/api/chat", data=dados, method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(requisicao, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))["message"]["content"]
        raise RuntimeError(
            f"Ollama respondeu {exc.code}: {detalhe[:300]}") from exc


LIMITE_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def gerar_conteudo(tarefa):
    texto = chamar_ollama([
        {"role": "system", "content": PROMPT_SISTEMA},
        {"role": "user", "content": montar_prompt(tarefa)},
    ])
    texto = LIMITE_THINK.sub("", texto).strip()
    if len(texto) < 200:
        raise RuntimeError(
            f"Texto gerado curto demais ({len(texto)} caracteres); "
            "descartando para nova tentativa.")
    return texto


def logar(mensagem):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {mensagem}", flush=True)


def recuperar_pendadas():
    """Estudos presos em 'em_producao' voltam para 'gerando'."""
    try:
        _, lista = chamar("GET", "/api/estudos?status=em_producao")
    except Exception as exc:
        logar(f"ERRO ao recuperar em_producao: {exc}")
        return
    for r in lista["estudos"]:
        chamar("POST", f"/api/tarefas/{r['id']}/falha",
               {"motivo": "recuperação após reinício do trabalhador"})
        logar(f"RECUPERADO: estudo #{r['id']} devolvido para 'gerando'")


def processar_tarefa(tarefa):
    eid = tarefa["id"]
    logar(f"GERANDO: estudo #{eid} — {tarefa['tema']} (modelo {MODELO})")
    try:
        conteudo = gerar_conteudo(tarefa)
    except Exception as exc:
        motivo = f"{type(exc).__name__}: {exc}"
        logar(f"FALHA na geração do estudo #{eid}: {motivo}")
        chamar("POST", f"/api/tarefas/{eid}/falha", {"motivo": motivo})
        return
    chamar("POST", f"/api/tarefas/{eid}/conteudo",
           {"conteudo": conteudo, "gerado_por": f"ollama:{MODELO}"})
    logar(f"ENTREGUE: estudo #{eid} ({len(conteudo)} caracteres)")


def ciclar():
    _, dados = chamar("GET", "/api/tarefas/pendentes")
    if not dados["pendentes"]:
        return
    _, res = chamar("POST", "/api/tarefas/reivindicar",
                    {"agente": AGENTE, "quantidade": len(dados["pendentes"])})
    for t in res["tarefas"]:
        processar_tarefa(t)


def main():
    parser = argparse.ArgumentParser(description="Trabalhador do agente de estudo")
    parser.add_argument("--uma-vez", action="store_true",
                        help="Processa o pendente atual e encerra")
    args = parser.parse_args()

    recuperar_pendadas()
    logar(f"Trabalhador iniciado. API em {os.getenv('AGENDA_API_URL', 'http://127.0.0.1:8800')} | "
          f"Ollama em {OLLAMA_URL} | modelo {MODELO}")
    if args.uma_vez:
        ciclar()
        logar("Ciclo único concluído.")
        return
    while True:
        try:
            ciclar()
        except urllib.error.URLError as exc:
            logar(f"API indisponível: {exc}")
        except Exception as exc:
            logar(f"ERRO no ciclo: {type(exc).__name__}: {exc}")
        time.sleep(INTERVALO)


if __name__ == "__main__":
    main()
