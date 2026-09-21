"""Camada de agentes da Veratus.

O pacote mantém a IA separada das integrações externas. O agente consulta dados
por ferramentas controladas e produz rascunhos; nenhum envio ao cliente acontece
automaticamente nesta versão.
"""

from .workflow import AgentConfigurationError, run_sales_workflow

__all__ = ["AgentConfigurationError", "run_sales_workflow"]
