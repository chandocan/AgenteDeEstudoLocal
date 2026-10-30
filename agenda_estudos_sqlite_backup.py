#!/usr/bin/env python3
"""
Agenda pessoal de estudos — servidor Linux
- Python 3 + SQLite, sem dependências externas.
- Mantém temas, data de geração, prazo e situação.
- Verifica tarefas a cada 60 segundos.
- Por enquanto, imprime notificações no log; e-mail será integrado depois.

Datas aceitas: YYYY-MM-DD HH:MM
Fuso horário: usa o fuso configurado no servidor.
"""

import argparse
import sqlite3
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "agenda_estudos.db"
LOG_PATH = BASE_DIR / "agenda_estudos.log"
CHECK_INTERVAL_SECONDS = 60


def agora():
    return datetime.now().replace(second=0, microsecond=0)


def conectar():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def criar_banco():
    with conectar() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS estudos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tema TEXT NOT NULL,
                topicos TEXT NOT NULL DEFAULT '',
                gerar_em TEXT NOT NULL,
                prazo_em TEXT NOT NULL,
                status_geracao TEXT NOT NULL DEFAULT 'aguardando',
                status_estudo TEXT NOT NULL DEFAULT 'pendente',
                lembrete_enviado INTEGER NOT NULL DEFAULT 0,
                alerta_atraso_enviado INTEGER NOT NULL DEFAULT 0,
                criado_em TEXT NOT NULL,
                concluido_em TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS eventos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                estudo_id INTEGER,
                tipo TEXT NOT NULL,
                mensagem TEXT NOT NULL,
                criado_em TEXT NOT NULL
            )
        """)


def registrar_evento(conn, estudo_id, tipo, mensagem):
    conn.execute(
        """INSERT INTO eventos (estudo_id, tipo, mensagem, criado_em)
           VALUES (?, ?, ?, ?)""",
        (estudo_id, tipo, mensagem, agora().isoformat(sep=" ")),
    )


def ler_data(valor):
    try:
        return datetime.strptime(valor, "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Use o formato YYYY-MM-DD HH:MM, por exemplo 2026-10-26 19:00."
        ) from exc


def adicionar(args):
    gerar = ler_data(args.gerar_em)
    prazo = ler_data(args.prazo_em)
    if prazo < gerar:
        raise SystemExit("Erro: o prazo de conclusão não pode ser anterior à data de geração.")

    with conectar() as conn:
        cur = conn.execute(
            """INSERT INTO estudos
               (tema, topicos, gerar_em, prazo_em, criado_em)
               VALUES (?, ?, ?, ?, ?)""",
            (
                args.tema.strip(),
                args.topicos.strip(),
                gerar.isoformat(sep=" "),
                prazo.isoformat(sep=" "),
                agora().isoformat(sep=" "),
            ),
        )
        estudo_id = cur.lastrowid
        registrar_evento(conn, estudo_id, "cadastro", f"Estudo cadastrado: {args.tema}")
    print(f"Estudo cadastrado com ID {estudo_id}.")


def listar(args):
    with conectar() as conn:
        if args.todos:
            rows = conn.execute("SELECT * FROM estudos ORDER BY gerar_em").fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM estudos
                   WHERE status_estudo != 'concluido'
                   ORDER BY gerar_em"""
            ).fetchall()

    if not rows:
        print("Nenhum estudo encontrado.")
        return

    for row in rows:
        print("-" * 60)
        print(f"ID: {row['id']} | Tema: {row['tema']}")
        print(f"Tópicos: {row['topicos'] or '(não informados)'}")
        print(f"Gerar em: {row['gerar_em']} | Prazo: {row['prazo_em']}")
        print(f"Geração: {row['status_geracao']} | Estudo: {row['status_estudo']}")


def concluir(args):
    with conectar() as conn:
        row = conn.execute("SELECT id, tema FROM estudos WHERE id=?", (args.id,)).fetchone()
        if not row:
            raise SystemExit(f"Não existe estudo com ID {args.id}.")
        conn.execute(
            """UPDATE estudos
               SET status_estudo='concluido', concluido_em=?
               WHERE id=?""",
            (agora().isoformat(sep=" "), args.id),
        )
        registrar_evento(conn, args.id, "conclusao", "Estudo marcado como concluído.")
    print(f"Estudo {args.id} marcado como concluído.")


def logar(mensagem):
    instante = agora().isoformat(sep=" ")
    linha = f"[{instante}] {mensagem}"
    print(linha, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as arquivo:
        arquivo.write(linha + "\n")


def verificar_agenda():
    momento = agora()
    with conectar() as conn:
        # Lembrete quando chega a data programada. A mensagem é registrada
        # uma única vez para evitar repetição a cada ciclo.
        lembretes = conn.execute(
            """SELECT * FROM estudos
               WHERE gerar_em <= ?
                 AND status_geracao = 'aguardando'
                 AND lembrete_enviado = 0""",
            (momento.isoformat(sep=" "),),
        ).fetchall()

        for row in lembretes:
            msg = (
                f"LEMBRETE: chegou a hora de gerar a aula #{row['id']} "
                f"— {row['tema']}. Prazo de conclusão: {row['prazo_em']}."
            )
            logar(msg)
            conn.execute(
                "UPDATE estudos SET lembrete_enviado=1 WHERE id=?", (row["id"],)
            )
            registrar_evento(conn, row["id"], "lembrete", msg)

        # Alerta se o prazo passou e o estudo ainda não foi concluído.
        atrasados = conn.execute(
            """SELECT * FROM estudos
               WHERE prazo_em < ?
                 AND status_estudo != 'concluido'
                 AND alerta_atraso_enviado = 0""",
            (momento.isoformat(sep=" "),),
        ).fetchall()

        for row in atrasados:
            msg = (
                f"ATRASO: estudo #{row['id']} — {row['tema']} — "
                f"prazo encerrado em {row['prazo_em']}; ainda não concluído."
            )
            logar(msg)
            conn.execute(
                "UPDATE estudos SET alerta_atraso_enviado=1 WHERE id=?", (row["id"],)
            )
            registrar_evento(conn, row["id"], "atraso", msg)


def executar_agendador(args):
    logar(f"Agendador iniciado. Banco: {DB_PATH}")
    while True:
        try:
            verificar_agenda()
        except Exception as exc:
            logar(f"ERRO ao verificar agenda: {type(exc).__name__}: {exc}")
        time.sleep(args.intervalo)


def mostrar_eventos(args):
    with conectar() as conn:
        rows = conn.execute(
            """SELECT * FROM eventos
               WHERE (? IS NULL OR estudo_id = ?)
               ORDER BY criado_em DESC LIMIT 100""",
            (args.id, args.id),
        ).fetchall()
    if not rows:
        print("Nenhum evento registrado.")
        return
    for row in rows:
        print(f"[{row['criado_em']}] estudo={row['estudo_id']} "
              f"tipo={row['tipo']}: {row['mensagem']}")


def main():
    criar_banco()
    parser = argparse.ArgumentParser(description="Agenda pessoal de estudos")
    sub = parser.add_subparsers(dest="comando", required=True)

    p_add = sub.add_parser("adicionar", help="Cadastrar um estudo")
    p_add.add_argument("--tema", required=True, help="Tema da aula")
    p_add.add_argument("--topicos", default="", help="Tópicos separados por ponto e vírgula")
    p_add.add_argument("--gerar-em", required=True, help="Data: YYYY-MM-DD HH:MM")
    p_add.add_argument("--prazo-em", required=True, help="Data: YYYY-MM-DD HH:MM")
    p_add.set_defaults(func=adicionar)

    p_list = sub.add_parser("listar", help="Listar estudos pendentes")
    p_list.add_argument("--todos", action="store_true", help="Incluir estudos concluídos")
    p_list.set_defaults(func=listar)

    p_done = sub.add_parser("concluir", help="Marcar estudo como concluído")
    p_done.add_argument("--id", type=int, required=True)
    p_done.set_defaults(func=concluir)

    p_run = sub.add_parser("executar", help="Executar verificação contínua")
    p_run.add_argument("--intervalo", type=int, default=CHECK_INTERVAL_SECONDS,
                       help="Intervalo entre verificações em segundos (padrão: 60)")
    p_run.set_defaults(func=executar_agendador)

    p_events = sub.add_parser("eventos", help="Ver histórico de eventos")
    p_events.add_argument("--id", type=int, default=None, help="Filtrar por ID de estudo")
    p_events.set_defaults(func=mostrar_eventos)

    args = parser.parse_args()
    if args.comando == "executar" and args.intervalo < 10:
        parser.error("O intervalo mínimo é 10 segundos.")
    args.func(args)


if __name__ == "__main__":
    main()

