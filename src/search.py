import os
import json
from dotenv import load_dotenv
from src.vectorstore import FaissVectorStore
from langchain_groq import ChatGroq

load_dotenv()

class RAGSearch:
    def __init__(self, persist_dir: str = "faiss_store",
                 embedding_model: str = "all-MiniLM-L6-v2",
                 llm_model: str = "openai/gpt-oss-20b",
                 data_dir: str | None = None):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not os.path.isabs(persist_dir):
            persist_dir = os.path.join(project_root, persist_dir)
        if data_dir is None:
            data_dir = os.path.join(project_root, "data")
        elif not os.path.isabs(data_dir):
            data_dir = os.path.join(project_root, data_dir)

        self.vectorstore = FaissVectorStore(persist_dir, embedding_model)
        # Rebuild the vector store when files in the data folder change.
        manifest_path = os.path.join(persist_dir, "data_manifest.json")
        supported_extensions = {".pdf", ".txt", ".csv", ".xlsx", ".docx", ".json"}
        current_manifest = []
        if os.path.isdir(data_dir):
            for root, _, filenames in os.walk(data_dir):
                for filename in filenames:
                    if os.path.splitext(filename)[1].lower() in supported_extensions:
                        path = os.path.join(root, filename)
                        stat = os.stat(path)
                        current_manifest.append({
                            "path": os.path.relpath(path, data_dir),
                            "size": stat.st_size,
                            "modified_ns": stat.st_mtime_ns,
                        })
        current_manifest.sort(key=lambda item: item["path"])

        faiss_path = os.path.join(persist_dir, "faiss.index")
        meta_path = os.path.join(persist_dir, "metadata.pkl")
        saved_manifest = None
        if os.path.exists(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as f:
                saved_manifest = json.load(f)

        index_exists = os.path.exists(faiss_path) and os.path.exists(meta_path)
        data_changed = current_manifest != saved_manifest
        if not index_exists or data_changed:
            from src.data_loader import load_all_documents
            docs = load_all_documents(data_dir)
            if not docs:
                raise ValueError(
                    "No documents found in the 'data' folder. Add supported files there first."
                )
            self.vectorstore.build_from_documents(docs)
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(current_manifest, f, indent=2)
        else:
            self.vectorstore.load()
        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            raise ValueError(
                "GROQ_API_KEY is missing. Add it to the project-root .env file."
            )
        self.llm = ChatGroq(groq_api_key=groq_api_key, model_name=llm_model)
        print(f"[INFO] Groq LLM initialized: {llm_model}")

    def search_and_summarize(self, query: str, top_k: int = 5) -> str:
        results = self.vectorstore.query(query, top_k=top_k)
        texts = [r["metadata"].get("text", "") for r in results if r["metadata"]]
        context = "\n\n".join(texts)
        if not context:
            return "No relevant documents found."
        prompt = f"""Summarize the following context for the query: '{query}'\n\nContext:\n{context}\n\nSummary:"""
        response = self.llm.invoke([prompt])
        return response.content

# Example usage
if __name__ == "__main__":
    rag_search = RAGSearch()
    query = "What is attention mechanism?"
    summary = rag_search.search_and_summarize(query, top_k=3)
    print("Summary:", summary)
