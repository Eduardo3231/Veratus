from __future__ import annotations

import argparse
import json
import sys

from .workflow import AgentConfigurationError, run_sales_workflow


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Executa o Sales Agent V1 da Veratus em modo de rascunho."
    )
    parser.add_argument("message", help="Mensagem recebida do cliente")
    parser.add_argument(
        "--customer", default="local-demo", help="Identificador interno do cliente"
    )
    parser.add_argument("--source", default="cli", help="Origem do contato")
    parser.add_argument("--product", default=None, help="Modelo sugerido")
    args = parser.parse_args()
    try:
        result = run_sales_workflow(
            customer_ref=args.customer,
            message=args.message,
            source=args.source,
            product_hint=args.product,
        )
    except AgentConfigurationError as exc:
        print(f"Configuração pendente: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
