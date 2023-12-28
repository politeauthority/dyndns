"""
    Quigley Notify
    Example method for sending a notification to the Quigley Api

"""
import json
import os
import requests


QUIGLEY_API_URL = os.environ.get("QUIGLEY_API_URL")
BASIC_AUTH = os.environ.get("QUIGLEY_API_BASIC_AUTH")


def send_notification(message: str, route: str = None):
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
    if not QUIGLEY_API_URL:
        print("ERROR: Missing env var: QUIGLEY_API_URL")
        return False
    if not route:
        route = "notify"
    headers = {
        "Authorization": "Basic %s" % BASIC_AUTH,
        "Content-Type": "application/json"
    }
    data = {
        "message": message
    }
    response = requests.post(
        "%s/%s" % (QUIGLEY_API_URL, route),
        data=json.dumps(data),
        headers=headers)
    if response.status_code in [200, 201]:
        print("Error sending notification")
        print(response.text)
    else:
        print("Notification sent successfully")

# End File: dyndns/src/modules/quigley-notify.py
