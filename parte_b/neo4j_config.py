"""Configuração de conexão com o Neo4j (lida de variáveis de ambiente / .env)."""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from neo4j import GraphDatabase

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# Dimensão dos embeddings. Tem que bater com o modelo usado para gerar os vetores:
#   384  -> sentence-transformers/all-MiniLM-L6-v2 ou paraphrase-multilingual-MiniLM-L12-v2
#   768  -> paraphrase-multilingual-mpnet-base-v2
#   1536 -> OpenAI text-embedding-3-small
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))


def get_driver():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
