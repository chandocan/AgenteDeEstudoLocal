#!/usr/bin/env python3
"""Cliente de teste da API da agenda — valida o canal etapa 2.

Simula o agente local: reivindica uma tarefa e devolve um conteúdo de
exemplo, sem Ollama/OpenCode (integração real é a etapa 3).

Uso (as variáveis de AGENDA_API_* e AGENDA_DB_* devem estar carregadas):
  python cliente_agente_teste.py saude
  python cliente_agente_teste.py pendentes
  python cliente_agente_teste.py reivindicar
  python cliente_agente_teste.py receber --id 2
  python cliente_agente_teste.py receber --id 2 --conteudo "texto..."
  python cliente_agente_teste.py falha --id 2 --motivo "ollama fora do ar"
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.getenv("AGENDA_API_URL", f"http://127.0.0.1:{os.getenv('AGENDA_API_PORT', '8800')}")
TOKEN = os.getenv("AGENDA_API_TOKEN", "")

CONTEUDO_EXEMPLO = """Aula de exemplo (cliente de teste, sem agente real).

Tema: {tema}
Tópicos: {topicos}

Introdução: este texto foi enviado via API para validar a etapa 2.
Quando o agente OpenCode + Ollama estiver integrado, este conteúdo
será produzido pelo modelo local.
"""


def chamar(metodo, caminho, corpo=None):
    requisicao = urllib.request.Request(
        BASE + caminho,
        data=None if corpo is None else json.dumps(corpo).encode("utf-8"),
        method=metodo,
        headers={
            "Content-Type": "application/json",
            **({"X-API-Token": TOKEN} if TOKEN else {}),
        },
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        sys.exit(f"ERRO: não foi possível conectar em {BASE}: {exc.reason}")


def mostrar(titulo, codigo, dados):
    print(f"{titulo} -> HTTP {codigo}")
    print(json.dumps(dados, indent=2, ensure_ascii=False, default=str))


def main():
    parser = argparse.ArgumentParser(description="Cliente de teste da API")
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("saude")
    sub.add_parser("pendentes")
    sub.add_parser("reivindicar").add_argument("--agente", default="teste-local")

    p = sub.add_parser("receber")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--conteudo", default=None,
                   help="Se omitido, envia o conteúdo de exemplo.")
    p.add_argument("--gerado-por", default="cliente-teste")

    p = sub.add_parser("falha")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--motivo", default="")

    args = parser.parse_args()

    if args.comando == "saude":
        mostrar("Saúde", *chamar("GET", "/api/saude"))
    elif args.comando == "pendentes":
        mostrar("Tarefas pendentes", *chamar("GET", "/api/tarefas/pendentes"))
    elif args.comando == "reivindicar":
        mostrar(
            "Reivindicação",
            *chamar(
                "POST",
                "/api/tarefas/reivindicar",
                {"agente": args.agente, "quantidade": 1},
            ),
        )
    elif args.comando == "receber":
        if args.conteudo:
            conteudo = args.conteudo
        else:
            _, lista = chamar("GET", "/api/estudos")
            alvo = next((e for e in lista["estudos"] if e["id"] == args.id), None)
            tema = alvo["tema"] if alvo else f"estudo {args.id}"
            topicos = (alvo or {}).get("topicos") or ""
            conteudo = CONTEUDO_EXEMPLO.format(tema=tema, topicos=topicos)
        mostrar(
            "Entrega de conteúdo",
            *chamar(
                "POST",
                f"/api/tarefas/{args.id}/conteudo",
                {"conteudo": conteudo, "gerado_por": args.gerado_por},
            ),
        )
    elif args.comando == "falha":
        mostrar(
            "Falha reportada",
            *chamar(
                "POST",
                f"/api/tarefas/{args.id}/falha",
                {"motivo": args.motivo},
            ),
        )


if __name__ == "__main__":
    main()
