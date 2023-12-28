"""
    Quigley Notify
    Example method for sending a notification to the Quigley Api

"""
import json
import os
import requests



BASIC_AUTH = os.environ.get("QUIGLEY_API_BASIC_AUTH")

def send_notification(data = dict, route: str = None):
    """Send a notifcation to the Quigley Api.
    :param data:
    {
        message: str message to be sent, this will show up in push notifications and shouldnt be
            formated.
        message_formatted: (str) Message to be sent, this can contain html. 
        room_id: (str) The full url for the Matrix room to send the message. 
            Note: the bot must be invited and accepted the room invite.
    }

    """
    if not BASIC_AUTH:
        print("ERROR: Missing env var: QUIGLEY_API_BASIC_AUTH")
        return False
    API_URL = "https://api.alix.lol"
    if not route:
        route = "notify"
    headers = {
        "Authorization": "Basic %s" % BASIC_AUTH,
        "Content-Type": "application/json"
    }
    response = requests.post(
        "%s/%s" % (API_URL, route),
        data=json.dumps(data),
        headers=headers)
    if response.status_code in [200, 201]:
        print("Error sending notification")
        print(response.text)
        # print(response.json())
    else:
        print("Notification sent successfully")

# End File: dyndns/src/modules/quigley-notify.py
