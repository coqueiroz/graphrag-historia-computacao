"""
Parte C — motor GraphRAG/KAG (recuperação híbrida, Text-to-Cypher, resposta citada,
confiança heurística e baseline RAG vetorial).

API pública para a Pessoa D (interface):

    from parte_c import responder_pergunta
    resultado = responder_pergunta("Quem criou o Unix?", modo="graphrag")

O retorno é um dict serializável em JSON (ver parte_c/docs/integracao_pessoa_d.md).
Nada neste pacote escreve no Neo4j: todas as sessões são abertas em modo READ.
"""
__all__ = ["responder_pergunta", "fechar"]


def __getattr__(name):  # import preguiçoso: `import parte_c.confidence` não carrega o driver/LLM
    if name in __all__:
        from parte_c import service

        return getattr(service, name)
    raise AttributeError(name)
