import os
from dotenv import load_dotenv
import chromadb

load_dotenv()

client = chromadb.CloudClient(
    api_key=os.environ.get("CHROMA_API_KEY"),
    tenant=os.environ.get("CHROMA_TENANT"),
    database=os.environ.get("CHROMA_DATABASE"),
)

collection = client.get_or_create_collection(name="test_collection")

collection.add(
    documents=["This is a test document for Chroma Cloud."],
    ids=["test1"],
)

results = collection.query(query_texts=["test"], n_results=1)
print(results)