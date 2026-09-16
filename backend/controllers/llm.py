"""
AI-assisted endpoints: semantic email Q&A and agentic MCP tool-calling.
"""

from fastapi import APIRouter, HTTPException

from backend import dependencies
from backend.databases.vector_database import query_vector_db
from backend.utilities.ask_ollama import llm_response, slm_response

router = APIRouter(tags=["llm"])


# This endpoint is called when the user types in the search bar and submits a query.
@router.get("/api/query")
async def query_vector_database(query: str, top_k: int = 3):
    """
    Query the vector database for relevant email content and generate AI response
    Returns an AI-generated answer based on the retrieved email context
    """
    try:
        if not query:
            raise HTTPException(status_code=400, detail="Query parameter is required")
        
        # ==================== RATE LIMITED LLM QUERY / global scope ====================
        result = dependencies.limiter.check(
             scope="global",
             identifier="all",
             endpoint="server_total",
             tokens = 2,
             capacity = 20,
             refill_rate = 20
        )

        if not result["allowed"]:
            raise HTTPException(
                 status_code=429,
                 detail=f"Rate limit exceeded! You can only view emails {result['limit']} times per hour. Wait {result['retry_after_seconds']} seconds.",
                 headers={
                     "X-RateLimit-Limit": str(result["limit"]),
                     "X-RateLimit-Remaining": "0",
                     "Retry-After": str(result["retry_after_seconds"])
                }
            )
        # ================================================================
        
        # Query the vector database (it's async)
        results = await query_vector_db(query, top_k=top_k)
        
        if not results:
            return {
                "status": "success",
                "answer": "I couldn't find any relevant emails to answer your question.",
                "sources": [],
                "count": 0
            }
        
        # Build context from retrieved emails
        context_parts = []
        sources = []
        
        for idx, doc in enumerate(results, 1):
            sender = doc.metadata.get("sender", "Unknown")
            subject = doc.metadata.get("subject", "No Subject")
            date = doc.metadata.get("date_sent", "Unknown date")
            content = doc.page_content 
            
            context_parts.append(f"Email {idx}:\nFrom: {sender}\nSubject: {subject}\nDate: {date}\nContent: {content}\n")
            
            sources.append({
                "message_id": doc.metadata.get("message_id", ""),
                "sender": sender,
                "subject": subject,
                "date_sent": date
            })
        
        context = "\n---\n".join(context_parts)
        
        # Create prompt for SLM
        prompt = f"""[INST]You are a helpful email assistant. Answer the user's question based on the provided email context.

                User Question: {query}

                Email Context:
                {context}

                Instructions:
                - Answer the question directly and concisely
                - Use information from the emails provided
                - If the emails don't contain enough information, say so
                - Be conversational and helpful
                - Do not make up information not present in the emails

                Answer:[/INST]"""
        
        # Get AI response
        try:
            #ai_response = slm_response(prompt)
            chatgpt_response = llm_response(prompt) # chatgpt response for testing.
            """
            # Extract text response (slm_response might return various formats)
            if isinstance(ai_response, str):
                answer = ai_response
            elif isinstance(ai_response, dict) and 'response' in ai_response:
                answer = ai_response['response']
            elif isinstance(ai_response, list):
                answer = str(ai_response)
            else:
                answer = str(ai_response)
            """
            if isinstance(chatgpt_response, str):
                answer = chatgpt_response
            elif isinstance(chatgpt_response, dict) and 'response' in chatgpt_response:
                answer = chatgpt_response['response']
            elif isinstance(chatgpt_response, list):
                answer = str(chatgpt_response)
            else:
                answer = str(chatgpt_response)
                
        except Exception as slm_error:
            print(f"SLM error: {slm_error}")
            answer = "I found relevant emails but encountered an error generating a response. Please try again."
        
        return {
            "status": "success",
            "answer": answer,
            "sources": sources,
            "count": len(sources)
        }
        
    except HTTPException:
        raise  # Re-raise HTTPExceptions (including 429 rate limit errors) without modification
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error querying vector database: {str(e)}")


@router.post("/api/llm-query")
async def llm_query_endpoint(request_data: dict):
    """
    Process natural language queries using LLM with access to MCP tools.
    The LLM can search emails, create calendar events, extract deadlines, and more.

    Request body:
    {
        "query": "Find deadlines in my emails and add them to calendar",
        "email_account_id": 1,
        "use_openai": true  // optional, defaults to true
    }
    """
    try:
        # ==================== RATE LIMITED LLM QUERY / global scope ====================
        result = dependencies.limiter.check(
             scope="global",
             identifier="all",
             endpoint="server_total",
             tokens = 2,
             capacity = 20,
             refill_rate = 20
        )

        if not result["allowed"]:
            raise HTTPException(
                 status_code=429,
                 detail=f"Rate limit exceeded! You can only view emails {result['limit']} times per hour. Wait {result['retry_after_seconds']} seconds.",
                 headers={
                     "X-RateLimit-Limit": str(result["limit"]),
                     "X-RateLimit-Remaining": "0",
                     "Retry-After": str(result["retry_after_seconds"])
                }
            )
        # ================================================================

        from backend.llm_integration import process_llm_query

        query = request_data.get("query")
        email_account_id = request_data.get("email_account_id")
        use_openai = request_data.get("use_openai", True)

        if not query:
            raise HTTPException(status_code=400, detail="query parameter is required")

        # Process the query through LLM with tool access
        result = await process_llm_query(query, user_id=email_account_id, use_openai=use_openai)

        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("error"))

        return {
            "status": "success",
            "answer": result.get("answer"),
            "actions": result.get("actions", []),
            "note": result.get("note")
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing LLM query: {str(e)}")
