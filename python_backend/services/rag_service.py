import json
import numpy as np
from .watsonx_service import generate_embedding
from .db_service import get_all_medical_knowledge

def cosine_similarity(v1, v2):
    if v1 is None or v2 is None or len(v1) != len(v2):
        return 0.0
    v1_arr = np.array(v1)
    v2_arr = np.array(v2)
    dot_product = np.dot(v1_arr, v2_arr)
    norm_v1 = np.linalg.norm(v1_arr)
    norm_v2 = np.linalg.norm(v2_arr)
    if norm_v1 == 0 or norm_v2 == 0:
        return 0.0
    return float(dot_product / (norm_v1 * norm_v2))

async def search_medical_guidelines(user_query, top_k=2):
    try:
        query_embedding = generate_embedding(user_query)
        if not query_embedding:
            return ""

        rows = get_all_medical_knowledge()
        if not rows:
            return ""

        scored_docs = []
        for row in rows:
            try:
                # Normalize keys (Db2 uppercase vs local lowercase)
                embedding_str = row.get('EMBEDDING') or row.get('embedding')
                title = row.get('TITLE') or row.get('title')
                content = row.get('CONTENT') or row.get('content')
                
                if not embedding_str: continue
                
                doc_vec = json.loads(embedding_str) if isinstance(embedding_str, str) else embedding_str
                score = cosine_similarity(query_embedding, doc_vec)
                scored_docs.append({
                    'title': title,
                    'content': content,
                    'score': score
                })
            except Exception as e:
                print(f"Error processing row: {e}")
                continue

        # Sort by score descending
        scored_docs.sort(key=lambda x: x['score'], reverse=True)
        top_docs = scored_docs[:top_k]

        context_str = "Relevant Medical Guidelines:\n"
        found = False
        for doc in top_docs:
            if doc['score'] > 0.6:
                found = True
                context_str += f"--- [{doc['title']}] ---\n{doc['content']}\n\n"

        return context_str.strip() if found else ""

    except Exception as e:
        print(f"RAG Retrieval Error: {e}")
        return ""
