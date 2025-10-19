from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document

# Load the local embeddings model
embeddings = OllamaEmbeddings(model="mxbai-embed-large")

db_location = "./vector_database"

collection = Chroma(
    persist_directory=db_location, 
    collection_name="mails",
    embedding_function=embeddings)


async def embed_and_store(mails: list[dict]):
    """Embed and store emails in the vector database
    
    Args:
        mails: List of email dictionaries with keys: message_id, sender, subject, date_sent, body_text
        Note: body_text should contain the cleaned email content
    """
    documents = []
    ids = []
    
    for mail in mails:
        # Create a Document object with the cleaned email body as content
        # and metadata for filtering/searching
        doc = Document(
            page_content=mail.get('body_text', ''),  # This contains cleaned content
            metadata={
                'message_id': mail.get('message_id', ''),
                'sender': mail.get('sender', ''),
                'subject': mail.get('subject', ''),
                'date_sent': str(mail.get('date_sent', ''))
            }
        )
        documents.append(doc)
        # Use the actual message_id as the unique identifier
        ids.append(mail.get('message_id', ''))
    
    # Add documents to the vector database
    if documents:
        collection.add_documents(documents, ids=ids)

async def query_vector_db(query: str, top_k: int = 2) -> list[Document]:
    """Query the vector database for similar documents
    
    Args:
        query: The query string to search for
        top_k: Number of top similar documents to retrieve"""
    
    embedded_query = embeddings.embed_query(query)

    results = collection.similarity_search_by_vector(   # The query method for Chroma with LangChain.
        embedded_query,
        k=top_k
    )

    return results

