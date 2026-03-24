import os
from functools import wraps
from flask import session
from datetime import datetime, timezone
from google.cloud import firestore

# Initialize Firestore lazily
db = None

def get_db():
    global db
    if db is None:
        try:
            # Relies on Application Default Credentials in Cloud Run automatically
            db = firestore.Client()
        except Exception as e:
            print(f"Failed to initialize Firestore: {e}")
    return db

def log_tool_usage(tool_name, status="success", error_message=""):
    """Logs a single usage event to Firestore in the background."""
    database = get_db()
    if not database: return

    try:
        user_email = session.get('user_email', 'unknown')
        doc_ref = database.collection('tool_usage').document()
        doc_ref.set({
            'tool_name': tool_name,
            'user_email': user_email,
            'timestamp': datetime.now(timezone.utc),
            'status': status,
            'error_message': str(error_message)[:500] # Limit size
        })
    except Exception as e:
        print(f"Error logging usage: {e}")

def track_usage(tool_name):
    """Decorator to automatically log tool usage on API endpoints."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            try:
                response = f(*args, **kwargs)
                
                # Safely extract HTTP status code from Flask response
                if isinstance(response, tuple) and len(response) > 1:
                    status_code = response[1]
                else:
                    status_code = getattr(response, 'status_code', 200)
                    
                status = "success" if 200 <= status_code < 400 else "error"
                log_tool_usage(tool_name, status=status)
                return response
            except Exception as e:
                log_tool_usage(tool_name, status="error", error_message=str(e))
                raise
        return decorated_function
    return decorator

def get_analytics_data():
    """Fetches and aggregates data for the admin Chart.js dashboard."""
    database = get_db()
    if not database: return {"error": "Database not configured."}
        
    try:
        # Fetch up to last 1000 actions
        docs = database.collection('tool_usage').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(1000).stream()
        
        tool_counts = {}
        dates_counts = {}
        status_counts = {"success": 0, "error": 0}
        
        for doc in docs:
            data = doc.to_dict()
            tool = data.get('tool_name', 'unknown')
            status = data.get('status', 'success')
            ts = data.get('timestamp')
            
            tool_counts[tool] = tool_counts.get(tool, 0) + 1
            status_counts[status] = status_counts.get(status, 0) + 1
            
            if ts:
                date_str = ts.strftime('%Y-%m-%d')
                dates_counts[date_str] = dates_counts.get(date_str, 0) + 1
                
        sorted_dates = sorted(dates_counts.keys())
        activity_data = {d: dates_counts[d] for d in sorted_dates}

        return {
            "tool_popularity": tool_counts,
            "activity_over_time": activity_data,
            "status_ratio": status_counts
        }
    except Exception as e:
        print(f"Error fetching analytics: {e}")
        return {"error": str(e)}