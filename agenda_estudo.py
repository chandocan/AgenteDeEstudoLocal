#!/usr/bin/env python3
"""Agendador pessoal de estudos com MySQL 8.

Configuração por variáveis de ambiente:
AGENDA_DB_HOST=localhost
AGENDA_DB_PORT=3306
AGENDA_DB_NAME=agenda_estudos
AGENDA_DB_USER=agenda_app
AGENDA_DB_PASSWORD=senha

As notificações são gravadas no MySQL como pendentes. O envio real por e-mail
e a integração com o agente local serão implementados posteriormente.
"""
import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import mysql.connector
    from mysql.connector import Error
except ImportError:
    print("ERRO: módulo mysql.connector não instalado para este Python.", file=sys.stderr)
    raise

BASE_DIR = Path(__file__).resolve().parent
LOG_PATH = BASE_DIR / "agenda_estudos.log"
INTERVALO_PADRAO = 60

DB = {
    "host": os.getenv("AGENDA_DB_HOST", "localhost"),
    "port": int(os.getenv("AGENDA_DB_PORT", "3306")),
    "user": os.getenv("AGENDA_DB_USER", "agenda_app"),
    "password": os.getenv("AGENDA_DB_PASSWORD", ""),
    "database": os.getenv("AGENDA_DB_NAME", "agenda_estudos"),
    "connection_timeout": 5,
    "autocommit": False,
}


def agora():
    return datetime.now().replace(second=0, microsecond=0)


def conectar():
    if not DB["password"]:
        raise RuntimeError("AGENDA_DB_PASSWORD não foi configurada.")
    return mysql.connector.connect(**DB)


def criar_tabelas():
    """Cria somente tabelas dentro do banco agenda_estudos."""
    comandos = [
        """
        CREATE TABLE IF NOT EXISTS estudos (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            tema VARCHAR(255) NOT NULL,
            topicos TEXT NULL,
            gerar_em DATETIME NOT NULL,
            prazo_em DATETIME NOT NULL,
            status_geracao VARCHAR(40) NOT NULL DEFAULT 'aguardando',
            status_estudo VARCHAR(40) NOT NULL DEFAULT 'pendente',
            lembrete_registrado TINYINT(1) NOT NULL DEFAULT 0,
            alerta_atraso_registrado TINYINT(1) NOT NULL DEFAULT 0,
            criado_em DATETIME NOT NULL,
            concluido_em DATETIME NULL,
            entregue_em DATETIME NULL,
            INDEX idx_geracao (status_geracao, gerar_em),
            INDEX idx_prazo (status_estudo, prazo_em)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS notificacoes (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            estudo_id BIGINT UNSIGNED NOT NULL,
            tipo VARCHAR(40) NOT NULL,
            canal VARCHAR(20) NOT NULL DEFAULT 'email',
            mensagem TEXT NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'pendente',
            criado_em DATETIME NOT NULL,
            enviado_em DATETIME NULL,
            INDEX idx_notif_status (status, criado_em),
            INDEX idx_notif_estudo (estudo_id),
            CONSTRAINT fk_notif_estudo FOREIGN KEY (estudo_id)
                REFERENCES estudos(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS historico_estudos (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            estudo_id BIGINT UNSIGNED NOT NULL,
            tipo VARCHAR(50) NOT NULL,
            mensagem TEXT NOT NULL,
            criado_em DATETIME NOT NULL,
            INDEX idx_hist_estudo (estudo_id, criado_em),
            CONSTRAINT fk_hist_estudo FOREIGN KEY (estudo_id)
                REFERENCES estudos(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS conteudos (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            estudo_id BIGINT UNSIGNED NOT NULL,
            conteudo LONGTEXT NOT NULL,
            gerado_por VARCHAR(80) NOT NULL DEFAULT 'agente_local',
            status VARCHAR(30) NOT NULL DEFAULT 'recebido',
            criado_em DATETIME NOT NULL,
            UNIQUE KEY uq_conteudo_estudo (estudo_id),
            CONSTRAINT fk_conteudo_estudo FOREIGN KEY (estudo_id)
                REFERENCES estudos(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
    ]
    conn = conectar()
    try:
        cur = conn.cursor()
        for sql in comandos:
            cur.execute(sql)
        conn.commit()
        cur.close()
    finally:
        conn.close()


def historico(conn, estudo_id, tipo, mensagem):
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO historico_estudos (estudo_id,tipo,mensagem,criado_em)
           VALUES (%s,%s,%s,%s)""",
        (estudo_id, tipo, mensagem, agora()),
    )
    cur.close()


def notificacao(conn, estudo_id, tipo, mensagem):
    # Fica pendente para a futura rotina de envio SMTP.
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO notificacoes
           (estudo_id,tipo,canal,mensagem,status,criado_em)
           VALUES (%s,%s,'email',%s,'pendente',%s)""",
        (estudo_id, tipo, mensagem, agora()),
    )
    cur.close()


def ler_data(valor):
    try:
        return datetime.strptime(valor, "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Formato esperado: YYYY-MM-DD HH:MM (ex.: 2026-11-02 19:00)."
        ) from exc


def adicionar(args):
    gerar, prazo = ler_data(args.gerar_em), ler_data(args.prazo_em)
    if prazo < gerar:
        raise SystemExit("O prazo não pode ser anterior à data de geração.")
    conn = conectar()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO estudos (tema,topicos,gerar_em,prazo_em,criado_em)
               VALUES (%s,%s,%s,%s,%s)""",
            (args.tema.strip(), args.topicos.strip(), gerar, prazo, agora()),
        )
        novo_id = cur.lastrowid
        historico(conn, novo_id, "cadastro", f"Estudo cadastrado: {args.tema.strip()}")
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"Estudo cadastrado com ID {novo_id}.")


def listar(args):
    conn = conectar()
    try:
        cur = conn.cursor(dictionary=True)
        if args.todos:
            cur.execute("SELECT * FROM estudos ORDER BY gerar_em")
        else:
            cur.execute(
                "SELECT * FROM estudos WHERE status_estudo <> 'concluido' ORDER BY gerar_em"
            )
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    if not rows:
        print("Nenhum estudo encontrado.")
        return
    for r in rows:
        print("-" * 60)
        print(f"ID: {r['id']} | Tema: {r['tema']}")
        print(f"Tópicos: {r['topicos'] or '(não informados)'}")
        print(f"Geração: {r['gerar_em']} | Prazo: {r['prazo_em']}")
        print(f"Status geração: {r['status_geracao']} | Estudo: {r['status_estudo']}")
        if r.get("entregue_em"):
            print(f"Aula entregue em: {r['entregue_em']}")
        concluido = r.get("concluido_em")
        if concluido:
            print(f"Concluído em: {concluido}")


def concluir(args):
    """Marca o estudo como concluído — independente de geração/entrega.

    Não é obrigatório que a aula esteja gerada ou entregue: o momento da
    conclusão guarda o contexto (status de geração e registro de entrega)
    para o histórico.
    """
    if args.data:
        momento = ler_data(args.data)
        if momento > agora():
            raise SystemExit("A data de conclusão não pode ser no futuro.")
    else:
        momento = agora()

    conn = conectar()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT id, tema, status_geracao, status_estudo, concluido_em,
                      entregue_em FROM estudos WHERE id=%s FOR UPDATE""",
            (args.id,),
        )
        r = cur.fetchone()
        cur.close()
        if not r:
            raise SystemExit(f"Não existe estudo com ID {args.id}.")
        if r["status_estudo"] == "concluido":
            raise SystemExit(
                f"Estudo {args.id} já está concluído (em {r['concluido_em']}). "
                "Use 'reabrir' para desmarcar antes de concluir novamente.")

        cur = conn.cursor()
        cur.execute(
            "UPDATE estudos SET status_estudo='concluido', concluido_em=%s WHERE id=%s",
            (momento, args.id),
        )
        cur.close()

        entrega = (
            f"entregue em {r['entregue_em']}" if r["entregue_em"]
            else "sem registro de entrega")
        observacao = f" Observação: {args.observacao}" if args.observacao else ""
        mensagem_hist = (
            f"Estudo concluído em {momento}. Contexto no momento da conclusão: "
            f"geração '{r['status_geracao']}', aula {entrega},{observacao}")
        historico(conn, args.id, "conclusao", mensagem_hist.strip())
        notificacao(
            conn, args.id, "estudo_concluido",
            (f"Estudo #{args.id} — {r['tema']} foi marcado como concluído "
             f"em {momento}. Geração: '{r['status_geracao']}'; aula {entrega}."))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"Estudo {args.id} marcado como concluído ({momento}). "
          f"Geração: {r['status_geracao']} | Aula: {entrega}.")


def reabrir(args):
    """Desfaz uma conclusão: o estudo volta para 'pendente'."""
    conn = conectar()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id, tema, status_estudo, concluido_em FROM estudos WHERE id=%s FOR UPDATE",
            (args.id,),
        )
        r = cur.fetchone()
        cur.close()
        if not r:
            raise SystemExit(f"Não existe estudo com ID {args.id}.")
        if r["status_estudo"] != "concluido":
            raise SystemExit(
                f"Estudo {args.id} não está concluído "
                f"(status atual: {r['status_estudo']}); reabertura ignorada.")
        cur = conn.cursor()
        cur.execute(
            """UPDATE estudos SET status_estudo='pendente', concluido_em=NULL
               WHERE id=%s""",
            (args.id,),
        )
        cur.close()
        historico(
            conn, args.id, "reabertura",
            f"Conclusão de {r['concluido_em']} desfeita; estudo de volta a 'pendente'.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"Estudo {args.id} reaberto (status 'pendente').")


def ver(args):
    """Exibe ou exporta o conteúdo gerado da aula de um estudo."""
    conn = conectar()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT e.tema, e.topicos, e.status_geracao, e.status_estudo,
                      c.conteudo, c.gerado_por, c.criado_em AS aula_em
               FROM estudos e
               LEFT JOIN conteudos c ON c.estudo_id = e.id
               WHERE e.id = %s""",
            (args.id,),
        )
        r = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not r:
        raise SystemExit(f"Não existe estudo com ID {args.id}.")
    if not r["conteudo"]:
        raise SystemExit(
            f"A aula do estudo {args.id} ainda não foi gerada "
            f"(status de geração: {r['status_geracao']}).")
    if args.exportar:
        Path(args.exportar).write_text(r["conteudo"], encoding="utf-8")
        print(f"Aula do estudo {args.id} exportada para {args.exportar}.")
        print(f"Tema: {r['tema']} | Gerado por: {r['gerado_por']} "
              f"em {r['aula_em']}")
    else:
        print("-" * 60)
        print(f"Estudo: {r['tema']}")
        print(f"Tópicos: {r['topicos'] or '(não informados)'}")
        print(f"Gerado por: {r['gerado_por']} | Status estudo: {r['status_estudo']}")
        print("-" * 60)
        print(r["conteudo"])


def logar(mensagem):
    linha = f"[{agora().isoformat(sep=' ')}] {mensagem}"
    print(linha, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(linha + "\n")


def verificar_agenda():
    conn = conectar()
    try:
        cur = conn.cursor(dictionary=True)
        momento = agora()

        # Estudos cuja data de geração chegou: sai de 'aguardando' e passa
        # para 'gerando'. O agente local (OpenCode + Ollama) consumirá os
        # estudos em 'gerando' em uma etapa futura deste pipeline.
        cur.execute(
            """SELECT * FROM estudos
               WHERE gerar_em <= %s AND status_geracao='aguardando'
               FOR UPDATE""",
            (momento,),
        )
        prontos = cur.fetchall()
        for r in prontos:
            if not r["lembrete_registrado"]:
                msg = (f"Hora de gerar a aula #{r['id']} — {r['tema']}. "
                       f"Prazo de conclusão: {r['prazo_em']}.")
                notificacao(conn, r["id"], "lembrete_geracao", msg)
                historico(conn, r["id"], "lembrete_geracao", msg)
                upd = conn.cursor()
                upd.execute("UPDATE estudos SET lembrete_registrado=1 WHERE id=%s", (r["id"],))
                upd.close()
                logar(f"LEMBRETE REGISTRADO: estudo #{r['id']} — {r['tema']}")

            upd = conn.cursor()
            upd.execute(
                "UPDATE estudos SET status_geracao='gerando' WHERE id=%s", (r["id"],)
            )
            upd.close()
            historico(conn, r["id"], "geracao_solicitada",
                      f"Geração do conteúdo solicitada para o agente local. "
                      f"Tópicos: {r['topicos'] or '(não informados)'}")
            logar(f"GERAÇÃO PROCESSADA: estudo #{r['id']} — "
                  f"{r['tema']} agora está em 'gerando' (aguardando o agente local).")

        cur.execute(
            """SELECT * FROM estudos
               WHERE prazo_em < %s AND status_estudo <> 'concluido'
                 AND alerta_atraso_registrado=0 FOR UPDATE""",
            (momento,),
        )
        for r in cur.fetchall():
            msg = (f"Prazo vencido para o estudo #{r['id']} — {r['tema']}. "
                   f"Prazo: {r['prazo_em']}; estudo ainda não concluído.")
            notificacao(conn, r["id"], "alerta_atraso", msg)
            historico(conn, r["id"], "alerta_atraso", msg)
            upd = conn.cursor()
            upd.execute(
                "UPDATE estudos SET alerta_atraso_registrado=1 WHERE id=%s", (r["id"],)
            )
            upd.close()
            logar(f"ALERTA DE ATRASO REGISTRADO: estudo #{r['id']} — {r['tema']}")

        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def executar(args):
    logar(f"Agendador iniciado. MySQL: {DB['host']}/{DB['database']}")
    while True:
        try:
            verificar_agenda()
        except Exception as exc:
            logar(f"ERRO ao verificar agenda: {type(exc).__name__}: {exc}")
        time.sleep(args.intervalo)


def processar(args=None):
    """Executa um único ciclo de verificação e encerra (uso manual/testes)."""
    logar("Ciclo único de verificação iniciado.")
    verificar_agenda()
    logar("Ciclo único de verificação concluído.")


def eventos(args):
    conn = conectar()
    try:
        cur = conn.cursor(dictionary=True)
        if args.id is None:
            cur.execute("SELECT * FROM historico_estudos ORDER BY criado_em DESC LIMIT 100")
        else:
            cur.execute(
                """SELECT * FROM historico_estudos WHERE estudo_id=%s
                   ORDER BY criado_em DESC LIMIT 100""",
                (args.id,),
            )
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    if not rows:
        print("Nenhum evento registrado.")
        return
    for r in rows:
        print(f"[{r['criado_em']}] estudo={r['estudo_id']} {r['tipo']}: {r['mensagem']}")


def notificacoes_pendentes(args):
    conn = conectar()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT id,estudo_id,tipo,status,criado_em,mensagem
               FROM notificacoes ORDER BY criado_em DESC LIMIT 100"""
        )
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    if not rows:
        print("Nenhuma notificação registrada.")
        return
    for r in rows:
        print(f"ID {r['id']} | estudo {r['estudo_id']} | {r['tipo']} | "
              f"{r['status']} | {r['criado_em']}\n{r['mensagem']}\n")


def main():
    parser = argparse.ArgumentParser(description="Agenda pessoal de estudos com MySQL")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("adicionar", help="Cadastrar estudo")
    p.add_argument("--tema", required=True)
    p.add_argument("--topicos", default="")
    p.add_argument("--gerar-em", required=True)
    p.add_argument("--prazo-em", required=True)
    p.set_defaults(func=adicionar)

    p = sub.add_parser("listar", help="Listar estudos")
    p.add_argument("--todos", action="store_true")
    p.set_defaults(func=listar)

    p = sub.add_parser("concluir", help="Concluir estudo")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--data", default=None,
                   help="Data/hora em que o estudo foi feito (YYYY-MM-DD HH:MM); "
                        "padrão: agora")
    p.add_argument("--observacao", default=None,
                   help="Texto livre para o histórico (dificuldade, dúvidas etc.)")
    p.set_defaults(func=concluir)

    p = sub.add_parser("reabrir", help="Desfazer a conclusão de um estudo")
    p.add_argument("--id", type=int, required=True)
    p.set_defaults(func=reabrir)

    p = sub.add_parser("ver", help="Ver/exportar o conteúdo gerado da aula")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--exportar", default=None,
                   help="Caminho do arquivo para exportar a aula (Markdown)")
    p.set_defaults(func=ver)

    p = sub.add_parser("executar", help="Iniciar agendador contínuo")
    p.add_argument("--intervalo", type=int, default=INTERVALO_PADRAO)
    p.set_defaults(func=executar)

    p = sub.add_parser("processar", help="Executar um único ciclo de verificação")
    p.set_defaults(func=processar)

    p = sub.add_parser("eventos", help="Consultar histórico")
    p.add_argument("--id", type=int, default=None)
    p.set_defaults(func=eventos)

    p = sub.add_parser("notificacoes", help="Consultar notificações registradas")
    p.set_defaults(func=notificacoes_pendentes)

    args = parser.parse_args()
    if args.comando == "executar" and args.intervalo < 10:
        parser.error("O intervalo mínimo é 10 segundos.")

    try:
        criar_tabelas()
        args.func(args)
    except Error as exc:
        print(f"Erro MySQL: {exc}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

