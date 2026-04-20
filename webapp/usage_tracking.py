import os
from functools import wraps
from flask import session
from datetime import datetime, timezone, timedelta
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
        docs = list(
            database.collection('tool_usage')
            .order_by('timestamp', direction=firestore.Query.DESCENDING)
            .limit(1000)
            .stream()
        )

        tool_counts = {}
        dates_counts = {}
        status_counts = {"success": 0, "error": 0}
        user_action_counts = {}      # email -> total action count
        user_first_seen = {}         # email -> earliest timestamp seen in this dataset
        tool_error_counts = {}       # tool -> error count
        per_tool_user_sets = {}      # tool -> set of unique user emails

        now = datetime.now(timezone.utc)
        this_week_start = now - timedelta(days=7)
        last_week_start = now - timedelta(days=14)
        this_week_count = 0
        last_week_count = 0

        for doc in docs:
            data = doc.to_dict()
            tool = data.get('tool_name', 'unknown')
            status = data.get('status', 'success')
            ts = data.get('timestamp')
            email = data.get('user_email', 'unknown')

            # Tool popularity
            tool_counts[tool] = tool_counts.get(tool, 0) + 1

            # Status overall
            status_counts[status] = status_counts.get(status, 0) + 1

            # Per-tool error counts
            if status == 'error':
                tool_error_counts[tool] = tool_error_counts.get(tool, 0) + 1

            # Unique users per tool
            if tool not in per_tool_user_sets:
                per_tool_user_sets[tool] = set()
            per_tool_user_sets[tool].add(email)

            # User action counts (for leaderboard)
            user_action_counts[email] = user_action_counts.get(email, 0) + 1

            if ts:
                date_str = ts.strftime('%Y-%m-%d')
                dates_counts[date_str] = dates_counts.get(date_str, 0) + 1

                # Week-on-week comparison
                if ts >= this_week_start:
                    this_week_count += 1
                elif ts >= last_week_start:
                    last_week_count += 1

                # Track earliest timestamp seen per user (proxy for first use)
                if email not in user_first_seen or ts < user_first_seen[email]:
                    user_first_seen[email] = ts

        # Sort dates for the activity chart
        sorted_dates = sorted(dates_counts.keys())
        activity_data = {d: dates_counts[d] for d in sorted_dates}

        # Unique users total
        unique_users = len(user_action_counts)

        # Week-on-week change
        wow_change = this_week_count - last_week_count
        wow_pct = round((wow_change / last_week_count * 100) if last_week_count > 0 else 0)

        # Top 10 users leaderboard
        top_users = sorted(user_action_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        leaderboard = [{"email": email, "count": count} for email, count in top_users]

        # Per-tool error rate (as a percentage, sorted by error count descending)
        tool_error_rates = {}
        for tool, total in tool_counts.items():
            errors = tool_error_counts.get(tool, 0)
            tool_error_rates[tool] = {
                "errors": errors,
                "total": total,
                "error_pct": round((errors / total * 100) if total > 0 else 0)
            }

        # New users this week (first seen in the last 7 days)
        new_users_this_week = sum(
            1 for ts in user_first_seen.values() if ts >= this_week_start
        )

        return {
            "tool_popularity": tool_counts,
            "activity_over_time": activity_data,
            "status_ratio": status_counts,
            "unique_users": unique_users,
            "this_week_count": this_week_count,
            "last_week_count": last_week_count,
            "wow_change": wow_change,
            "wow_pct": wow_pct,
            "leaderboard": leaderboard,
            "tool_error_rates": tool_error_rates,
            "new_users_this_week": new_users_this_week,
        }

    except Exception as e:
        print(f"Error fetching analytics: {e}")
        return {"error": str(e)}
