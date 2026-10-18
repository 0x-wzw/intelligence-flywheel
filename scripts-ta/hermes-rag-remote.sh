#!/bin/bash
# Entry point for REMOTE MCP clients reaching this Mac's RAG index over SSH.
#
#   ssh admin@100.93.18.120 /Users/admin/.hermes/scripts/hermes-rag-remote.sh
#
# SSH provides the transport, encryption and authentication, so nothing new
# listens on the network and there is no auth scheme to invent or leak. The
# server speaks MCP over stdio; ssh just carries that stdio.
#
# Read-only by design — see hermes_rag/mcp_server_readonly.py for why (single-writer
# ingest, destructive delete, and TCC blocking remote reads of ~/Documents anyway).
#
# `ssh host <cmd>` gives a minimal non-interactive environment, so everything is
# absolute and PYTHONPATH is set explicitly: the package lives at /Users/admin/
# and is NOT installed into the venv.
set -uo pipefail

export PYTHONPATH=/Users/admin
export PATH=/usr/local/bin:/usr/bin:/bin
export HOME=/Users/admin
# All inference (embeddings + chat) goes through the Ilmu API. The key lives in
# ~/.hermes/.env (ILMU_API_KEY) — source it if not already in the environment.
if [ -z "${ILMU_API_KEY:-}" ] && [ -z "${HERMES_ILMU_KEY:-}" ]; then
    while IFS= read -r line; do
        case "$line" in
            ILMU_API_KEY=*) export ILMU_API_KEY="${line#ILMU_API_KEY=}" ;;
        esac
    done < /Users/admin/.hermes/.env
fi

cd /tmp || exit 1   # git/python getcwd must not start in a TCC-protected dir

exec /Users/admin/rag-env312/bin/python -m hermes_rag.mcp_server_readonly
