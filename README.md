<div align="center">

# 🤖 Agente De Estudo Local

**Study-scheduling system with a local LLM worker — autonomous pipeline: scheduler → REST API → Ollama agent → e-mail delivery.**

Python · FastAPI · MySQL · systemd · Ollama

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![MySQL](https://img.shields.io/badge/DB-MySQL-4479A1?logo=mysql)](https://mysql.com)
[![Ollama](https://img.shields.io/badge/LLM-Ollama_local-black)](https://ollama.com)
[![systemd](https://img.shields.io/badge/Services-systemd-D4380F)](https://freedesktop.org/wiki/Software/systemd/)

</div>

---

## ✨ What it does (English)

An autonomous study-and-content pipeline that runs on a personal machine, fully offline:

1. **Scheduler** (`agenda_estudo.py`) keeps a study agenda in MySQL — themes, topics, generation dates, deadlines, notifications and history. As a systemd service, it continuously checks scheduled items and moves them through a generation lifecycle.
2. **REST API** (`api_agenda.py`, FastAPI + uvicorn) is the contract between the server and the local LLM worker. Task claiming uses **`FOR UPDATE SKIP LOCKED`** so work items can't be double-claimed, with retries (`/falha` puts items back in queue).
3. **LLM worker** (`agente_trabalhador.py`) polls the API, claims pending tasks, generates the full lesson locally with **Ollama** (no cloud calls), and posts the result back. Crashes are self-recovering: anything stuck in-progress is re-queued at startup.
4. **E-mail delivery** (`envia_emails.py`, stdlib `smtplib`) sends the generated lesson embedded in the e-mail (plain text + HTML converted from Markdown), with retry counters and error tracking in the database.

Every step is supervised by its own **systemd unit**, and all credentials come from environment variables — **no secrets in the codebase**.

### Generation lifecycle

```
aguardando ──▶ gerando ──▶ em_producao ──▶ gerado   (aula no banco + e-mail)
                 ▲              │
                 └──── falha ◀──┘   (volta para fila, tentativas registradas)
```

## 🇧🇷 O que ele faz (Português)

Sistema autônomo de agenda de estudos com **produção de conteúdo por LLM local** — agendador em MySQL (serviço systemd), **API REST em FastAPI** com controle de concorrência (`FOR UPDATE SKIP LOCKED`), **trabalhador que gera aulas com Ollama rodando na própria máquina** (100% offline) e **entrega por e-mail** com o conteúdo embutido (texto + HTML convertido do Markdown). Falhas retornam à fila automaticamente, com contadores de tentativa e histórico no banco.

Detalhes completos de operação, comandos e testes validados: **[docs/OPERACAO.md](docs/OPERACAO.md)**

## 🧭 Architecture

```
┌ systemd supervision ────────────────────────────────────────────┐
│                                                                 │
│  agenda-estudos.service        agenda-estudos-api.service       │
│  ┌───────────────────┐  move   ┌──────────────────────────┐     │
│  │ agendador (loop)  │────────▶│ REST API (FastAPI :8800) │     │
│  └───────────────────┘  status │ FOR UPDATE SKIP LOCKED   │     │
│                                └──────┬───────────────────┘     │
│                    claim/conteudo/falha│                        │
│                                ┌──────▼───────────────────┐     │
│  agenda-agente.service         │ MySQL                     │◀────│── agenda-emails.service
│  ┌───────────────────┐         └──────────────────────────┘     │  envia aula por SMTP
│  │ trabalhador       │──── Ollama (qwen/llama local) ────┐     │  com retry + erro
│  └───────────────────┘                                   │     │
└─────────────────────────────────────────────────────────┼──────┘
                                                          ▼
                                                   aula gerada (offline)
```

## 🛠 Tech highlights

- **Concurrent-safe queue:** `SELECT ... FOR UPDATE SKIP LOCKED` + state machine (`aguardando → gerando → em_producao → gerado`)
- **Crash recovery:** worker re-queues stale `em_producao` items at startup; e-mail worker re-queues stuck `enviando`
- **Secrets management:** everything via `EnvironmentFile` — repo contains only `agenda-estudos.conf.example`
- **Tested end-to-end:** real e-mail delivery validated (6 notifications sent with delivery timestamps), generated lessons (~8.5k chars with a 4B local model), duplicate-conclusion protection, reopenable studies with backdated context

## 🚀 Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# configure (see agenda-estudos.conf.example) — expects local MySQL + Ollama
cp agenda-estudos.conf.example ~/.agenda-estudos.conf

# CLI
python agenda_estudo.py --help
python agenda_estudo.py adicionar --tema "..." --topicos "..." --gerar-em "2026-11-03 08:00" --prazo-em "2026-11-05 18:00"
python agenda_estudo.py processar   # single scheduler cycle

# API
python api_agenda.py                # http://127.0.0.1:8800/api/saude

# LLM worker (one shot) and e-mail worker (one shot)
python agente_trabalhador.py --uma-vez
python envia_emails.py --uma-vez
```

Systemd units are included (`agenda-estudos.service`, `agenda-estudos-api.service`, `agenda-agente.service`, `agenda-emails.service`) — see [docs/OPERACAO.md](docs/OPERACAO.md) for installation and service management.

## 📋 Requirements

- Python 3.12
- MySQL 8 (local)
- [Ollama](https://ollama.com) with a small model (e.g. `qwen3.5:4b` — validated on CPU)

---

## 👤 Author

**Jose Alfredo** — backend developer · Python · APIs · applied AI (local LLMs)
GitHub: [@chandocan](https://github.com/chandocan) · LinkedIn: [alfredo-santos-filho](https://www.linkedin.com/in/alfredo-santos-filho-2b467b127)
