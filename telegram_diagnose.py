import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

import requests


def tg_call(token: str, method: str, params: Optional[Dict[str, Any]] = None, timeout: int = 20) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        resp = requests.get(url, params=params or {}, timeout=timeout)
        data = resp.json()
    except requests.RequestException as e:
        return {"ok": False, "error": f"Falha de rede ao chamar {method}: {e}"}
    except json.JSONDecodeError:
        return {"ok": False, "error": f"Resposta não-JSON do Telegram em {method}: HTTP {resp.status_code}", "text": resp.text[:500]}
    return data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnóstico rápido da comunicação com o Telegram (token, webhook, updates).",
    )
    parser.add_argument("--delete-webhook", action="store_true", help="Remove o webhook (útil se o bot usa polling).")
    parser.add_argument("--get-updates", action="store_true", help="Tenta buscar updates pendentes (pare o bot antes).")
    args = parser.parse_args()

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        print("ERRO: variável de ambiente BOT_TOKEN não está definida.")
        print('Exemplo (PowerShell): $env:BOT_TOKEN="123:ABC"')
        return 2

    print("== Telegram API: getMe ==")
    me = tg_call(token, "getMe")
    print(json.dumps(me, ensure_ascii=False, indent=2))
    if not me.get("ok"):
        print("\nFalha em getMe. Isso geralmente significa TOKEN inválido ou bloqueio de rede/SSL.")
        return 1

    print("\n== Telegram API: getWebhookInfo ==")
    wh = tg_call(token, "getWebhookInfo")
    print(json.dumps(wh, ensure_ascii=False, indent=2))
    if wh.get("ok"):
        url = (wh.get("result") or {}).get("url") or ""
        if url:
            print(f"\nATENÇÃO: há um webhook configurado: {url}")
            print("Se você está rodando com polling (run_polling), recomendo remover o webhook.")
        else:
            print("\nOK: nenhum webhook configurado (compatível com polling).")

    if args.delete_webhook:
        print("\n== Telegram API: deleteWebhook ==")
        res = tg_call(token, "deleteWebhook", params={"drop_pending_updates": "true"})
        print(json.dumps(res, ensure_ascii=False, indent=2))

    if args.get_updates:
        print("\n== Telegram API: getUpdates (pare o bot antes) ==")
        updates = tg_call(token, "getUpdates", params={"limit": 5, "timeout": 0})
        print(json.dumps(updates, ensure_ascii=False, indent=2))
        if updates.get("ok"):
            results = updates.get("result") or []
            print(f"\nTotal retornado: {len(results)}")
            if results:
                print("Se isso retorna updates, o Telegram está entregando mensagens; o problema pode estar no seu processo rodando no Render.")

    print("\nDiagnóstico concluído.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


