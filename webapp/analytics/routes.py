@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    data = request.json
    # Note: We are now fetching broadly to ensure we get all 'legs' of a call
    # instead of just the leg matching a specific user/queue
    time_settings = {
        "timeZone": data.get('timeZone', 'UTC'),
        "timeRange": {
            "timeFrom": data.get('timeFrom'),
            "timeTo": data.get('timeTo')
        }
    }

    rc_analytics = RCBusinessAnalytics()

    try:
        # We fetch for 'Company' dimension to ensure we get every session in the account
        # this allows the frontend to stitch transfers together.
        result = rc_analytics.fetch_records(
            dimension="Company", 
            time_settings=time_settings,
            page=1,
            per_page=250 # Increased to get more context for stitching
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
