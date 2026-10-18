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
# Ollama is launchd-managed on this host; the server needs it reachable for
# embeddings. Do not point this at a remote Ollama — the index was built with
# nomic-embed-text at 768 dims and a different model means a full rebuild.
export OLLAMA_HOST=127.0.0.1:11434

cd /tmp || exit 1   # git/python getcwd must not start in a TCC-protected dir

exec /Users/admin/rag-env312/bin/python -m hermes_rag.mcp_server_readonly
