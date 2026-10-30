#!/usr/bin/env python3
"""Envio real de e-mails das notificações da agenda (smtplib).

Consome as notificações com canal='email' e status='pendente' do banco:
envia via SMTP (Gmail por padrão) e marca 'enviado' com enviado_em. Em
falha, incrementa tentativas; após o limite, marca 'erro' com detalhe.
Na inicialização, devolve para 'pendente' tudo que ficou preso em
'enviando' (envio interrompido).

Variáveis de ambiente:
  AGENDA_SMTP_HOST     (padrão smtp.gmail.com)
  AGENDA_SMTP_PORT     (padrão 587, STARTTLS)
  AGENDA_SMTP_USER     (conta Gmail remetente)
  AGENDA_SMTP_PASSWORD (senha de app do Gmail — NÃO é a senha da conta)
  AGENDA_EMAIL_DE      (padrão: AGENDA_SMTP_USER)
  AGENDA_EMAIL_PARA    (padrão: AGENDA_EMAIL_DE)
  AGENDA_EMAIL_INTERVALO (padrão 60 segundos entre varreduras)
  AGENDA_EMAIL_TENTATIVAS (padrão 5, até marcar 'erro')

Execução manual one-shot (para testes): --uma-vez envia o pendente e
encerra.
"""
import argparse
import os
import sys
import time
from email.message import EmailMessage
from email.utils import formataddr

import smtplib
import ssl

from agenda_estudo import agora, conectar, criar_tabelas, logar

try:
    import markdown as md_lib
except ImportError:
    md_lib = None

SMTP_HOST = os.getenv("AGENDA_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("AGENDA_SMTP_PORT", "587"))
SMTP_USER = os.getenv("AGENDA_SMTP_USER", "")
SMTP_PASSWORD = os.getenv("AGENDA_SMTP_PASSWORD", "")
EMAIL_DE = os.getenv("AGENDA_EMAIL_DE", SMTP_USER)
EMAIL_PARA = os.getenv("AGENDA_EMAIL_PARA", EMAIL_DE)
INTERVALO = int(os.getenv("AGENDA_EMAIL_INTERVALO", "60"))
LIMITE_TENTATIVAS = int(os.getenv("AGENDA_EMAIL_TENTATIVAS", "5"))
LOTE = int(os.getenv("AGENDA_EMAIL_LOTE", "20"))

TIPO_LEGIVEL = {
    "lembrete_geracao": "Lembrete de geração",
    "alerta_atraso": "Alerta de atraso",
    "conteudo_disponivel": "Aula pronta",
    "estudo_concluido": "Estudo concluído",
}

ASSINATURA = (
    "\n\n--\nMensagem automática da Agenda de Estudos. "
    "Não responda este e-mail.")


def garantir_colunas():
    """Adiciona tentativas/erro em notificacoes se não existirem."""
    conn = conectar()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT COLUMN_NAME FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA=%s AND TABLE_NAME='notificacoes'""",
            (conn.database,),
        )
        existentes = {r[0] for r in cur.fetchall()}
        cur.close()
        if "tentativas" not in existentes:
            cur = conn.cursor()
            cur.execute(
                """ALTER TABLE notificacoes
                   ADD COLUMN tentativas INT NOT NULL DEFAULT 0""")
            cur.close()
        if "erro" not in existentes:
            cur = conn.cursor()
            cur.execute("ALTER TABLE notificacoes ADD COLUMN erro TEXT NULL")
            cur.close()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def recuperar_enviando():
    conn = conectar()
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE notificacoes SET status='pendente'
               WHERE status='enviando'""")
        afetadas = cur.rowcount
        cur.close()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if afetadas:
        logar(f"RECUPERACAO: {afetadas} notificação(ões) em 'enviando' "
              "devolvidas para 'pendente'.")


def buscar_conteudo(estudo_id):
    """Puxa a aula gerada (tabela conteudos) para embutir no e-mail."""
    conn = conectar()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT c.conteudo, e.tema FROM conteudos c
               JOIN estudos e ON e.id = c.estudo_id
               WHERE c.estudo_id = %s""",
            (estudo_id,),
        )
        linha = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    return linha


def montar_email(destino, linha):
    """Monta o e-mail; 'conteudo_disponivel' recebe a aula embutida
    (texto simples + HTML quando o conversor Markdown está disponível).
    """
    tipo = TIPO_LEGIVEL.get(linha["tipo"], linha["tipo"])
    msg = EmailMessage()
    msg["From"] = formataddr(("Agenda de Estudos", EMAIL_DE))
    msg["To"] = destino
    msg["Subject"] = (
        f"[Agenda de Estudos] {tipo} — estudo #{linha['estudo_id']}")

    embutido = None
    if linha["tipo"] == "conteudo_disponivel":
        embutido = buscar_conteudo(linha["estudo_id"])

    if embutido:
        corpo = (
            linha["mensagem"] + ASSINATURA
            + "\n\n=== CONTEÚDO DA AULA ===\n\n" + embutido["conteudo"])
        msg.set_content(corpo)
        if md_lib is not None:
            bloco = md_lib.markdown(embutido["conteudo"], extensions=["fenced_code"])
            html = (
                "<div style=\"font-family: system-ui, sans-serif; "
                "max-width: 42rem; margin: 0 auto; line-height: 1.45;\">"
                "<h1>Aula: " + str(embutido["tema"]) + "</h1>"
                + bloco + "</div>"
            )
            msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(linha["mensagem"] + ASSINATURA)
    return msg


def enviar_lote():
    """Envia até LOTE notificações pendentes.

    Retorna (enviadas, restantes): 'restantes' > 0 sinaliza que houve
    falha e ficou pendente depois do lote.
    """
    conn = conectar()
    smtp = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT * FROM notificacoes
               WHERE canal='email' AND status='pendente'
               ORDER BY criado_em LIMIT %s FOR UPDATE""",
            (LOTE,),
        )
        linhas = cur.fetchall()
        if not linhas:
            cur.close()
            conn.commit()
            return 0, 0
        for linha in linhas:
            claim = conn.cursor()
            claim.execute(
                "UPDATE notificacoes SET status='enviando' WHERE id=%s",
                (linha["id"],),
            )
            claim.close()
        conn.commit()  # libera o lock; IDs já reivindicados
        cur.close()
    finally:
        conn.close()

    enviadas = 0
    try:
        for linha in linhas:
            mensagem = montar_email(EMAIL_PARA, linha)
            if smtp is None:
                smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(mensagem)
            enviadas += 1
            conn = conectar()
            try:
                cur = conn.cursor()
                cur.execute(
                    """UPDATE notificacoes
                       SET status='enviado', enviado_em=%s WHERE id=%s""",
                    (agora(), linha["id"]),
                )
                cur.close()
                if linha["tipo"] == "conteudo_disponivel":
                    # Marca a entrega da aula (uma vez só) — eixo independente
                    # do "concluido" do usuário.
                    cur = conn.cursor()
                    cur.execute(
                        """UPDATE estudos SET entregue_em=%s
                           WHERE id=%s AND entregue_em IS NULL""",
                        (agora(), linha["estudo_id"]),
                    )
                    cur.close()
                conn.commit()
            finally:
                conn.close()
            logar(f"ENVIADO: notificação #{linha['id']} "
                  f"({linha['tipo']}, estudo {linha['estudo_id']}) "
                  f"para {EMAIL_PARA}")
    except Exception as exc:
        errando = f"{type(exc).__name__}: {exc}"
        logar(f"ERRO de envio: {errando}")
        conn = conectar()
        try:
            for linha in linhas[enviadas:]:
                cur = conn.cursor()
                cur.execute(
                    """UPDATE notificacoes
                       SET tentativas = tentativas + 1, erro=%s,
                           status = IF(tentativas + 1 >= %s, 'erro', 'pendente')
                       WHERE id=%s""",
                    (errando, LIMITE_TENTATIVAS, linha["id"]),
                )
                cur.close()
            conn.commit()
        finally:
            conn.close()
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass
    return enviadas, len(linhas) - enviadas


def ciclar():
    """Envia até esgotar o pendente (em lotes de LOTE).

    Para também quando um lote falha sem enviar nada: não adianta
    martelar na mesma falha em loop.
    """
    while True:
        enviadas, restantes = enviar_lote()
        if enviadas == 0 or restantes == 0:
            return


def main():
    parser = argparse.ArgumentParser(description="Envio de e-mails da agenda")
    parser.add_argument("--uma-vez", action="store_true",
                        help="Envia o pendente atual e encerra")
    args = parser.parse_args()

    if not SMTP_USER or not SMTP_PASSWORD:
        sys.exit(
            "ERRO de configuração: AGENDA_SMTP_USER e AGENDA_SMTP_PASSWORD "
            "devem estar definidas (senha de app do Gmail).")
    if not EMAIL_DE or not EMAIL_PARA:
        sys.exit("ERRO de configuração: remetente/destinatário não definidos.")

    criar_tabelas()
    garantir_colunas()
    recuperar_enviando()
    logar(f"Envio de e-mails iniciado. SMTP: {SMTP_HOST}:{SMTP_PORT} | "
          f"De: {EMAIL_DE} | Para: {EMAIL_PARA}")
    if args.uma_vez:
        ciclar()
        logar("Varredura única concluída.")
        return
    while True:
        try:
            ciclar()
        except Exception as exc:
            logar(f"ERRO no ciclo de e-mail: {type(exc).__name__}: {exc}")
        time.sleep(INTERVALO)


if __name__ == "__main__":
    main()
