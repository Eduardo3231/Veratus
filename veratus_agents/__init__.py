"""Camada de agentes da Veratus.

O pacote mantém a IA separada das integrações externas. O agente consulta dados
por ferramentas controladas e produz rascunhos. O envio automático depende do
nível de cada canal (``CONVERSATION_MODE_<CANAL>``, padrão ``SHADOW``) e das
chaves de envio, todas desligadas por padrão.
"""

from typing import Any

__all__ = ["AgentConfigurationError", "run_sales_workflow"]


def __getattr__(name: str) -> Any:
    # Import tardio: ``python -m veratus_agents.<módulo>`` não carrega o
    # workflow (e o SDK de agentes) só para rodar uma ferramenta de linha de comando.
    if name in __all__:
        from . import workflow

        return getattr(workflow, name)
    raise AttributeError(name)
