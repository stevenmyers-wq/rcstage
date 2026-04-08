@analytics_bp.route('/api/analytics/records', methods=['POST'])
def get_call_records():
    data = request.json
    target_account = data.get('targetAccountId') or session.get('active_analytics_id')
    
    if not target_account:
        return jsonify({"error": "No target account specified."}), 400

    rc_analytics = RCBusinessAnalytics(account_id=target_account)

    try:
        result = rc_analytics.fetch_records(
            dimension=data.get('dimension', 'Queues'),
            time_settings={
                "timeZone": data.get('timeZone', 'UTC'),
                "timeRange": {"timeFrom": data.get('timeFrom'), "timeTo": data.get('timeTo')}
            }
        )
        
        # CRITICAL FIX: If rc_api_call returns None or an error object, handle it here
        if result is None:
            return jsonify({"error": "RingCentral API returned no data. Check if the app is 'Connected' in the header."}), 500
        
        if 'error' in result:
            return jsonify({"error": f"RC API Error: {result.get('error_description', result['error'])}"}), 500

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
