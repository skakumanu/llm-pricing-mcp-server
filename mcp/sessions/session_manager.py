"""Session manager for MCP server context management."""
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import uuid


class SessionManager:
    """Manages MCP session state and context."""
    
    def __init__(self):
        """Initialize the session manager."""
        self.sessions: Dict[str, Dict[str, Any]] = {}
    
    def create_session(self, client_id: Optional[str] = None) -> str:
        """
        Create a new session.
        
        Args:
            client_id: Optional client identifier
            
        Returns:
            Session ID
        """
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = {
            "id": session_id,
            "client_id": client_id or "unknown",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_activity": datetime.now(timezone.utc).isoformat(),
            "context": {},
        }
        return session_id
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session by ID."""
        return self.sessions.get(session_id)
    
    def update_session(self, session_id: str, context: Dict[str, Any]) -> bool:
        """Update session context."""
        if session_id not in self.sessions:
            return False
        
        self.sessions[session_id]["context"].update(context)
        self.sessions[session_id]["last_activity"] = datetime.now(timezone.utc).isoformat()
        return True
    
    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        if session_id in self.sessions:
            del self.sessions[session_id]
            return True
        return False
    
    def list_sessions(self) -> list:
        """List all active sessions."""
        return list(self.sessions.values())
