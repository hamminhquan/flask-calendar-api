# -*- coding: utf-8 -*-
import eventlet
eventlet.monkey_patch()

from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import json
import os
import time
import requests  # 🔥 추가됨
import xml.etree.ElementTree as ET  # 🔥 추가됨
from datetime import datetime, timedelta
from threading import Thread, Event
from datetime import datetime
import pytz
from dateutil import parser

app = Flask(__name__)	
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
CORS(app, supports_credentials=True, resources={r"/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*")

DATA_FILE = "events.json"
IMPORTANCE_DATA_FILE = "importance_learning.json"
running_threads = {}

# ✅ 일정 데이터 불러오기 함수
def load_events():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []  # 일정 데이터가 없을 경우 빈 리스트 반환

# ✅ 일정 데이터 저장하기 함수
def save_events(events):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=4)

# ✅ 구글 뉴스 RSS에서 뉴스 가져오기
def fetch_google_news(query):
    print(f"🔍 뉴스 검색 요청: {query}")
    url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"

    try:
        response = requests.get(url)
        if response.status_code != 200:
            print("❌ 뉴스 가져오기 실패:", response.status_code)
            return []

        root = ET.fromstring(response.content)
        articles = []

        for item in root.findall(".//item"):
            title = item.find("title").text
            link = item.find("link").text
            pub_date_str = item.find("pubDate").text  # 예: "Wed, 27 Mar 2025 10:00:00 GMT"

            # ✅ 날짜 변환 (GMT → 한국 시간)
            pub_date = datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S %Z")
            pub_date = pub_date.astimezone(pytz.timezone("Asia/Seoul"))
            pub_date_str = pub_date.strftime("%Y-%m-%d")  # "2025-03-27"

            articles.append({"title": title, "url": link, "date": pub_date_str})

        print(f"📢 가져온 뉴스 개수: {len(articles)}")
        return articles[:5]  # 최신 뉴스 5개만 반환

    except Exception as e:
        print(f"❌ 뉴스 가져오기 오류: {e}")
        return []


@app.route('/save_keywords', methods=['OPTIONS', 'POST'])
def save_keywords():
    if request.method == 'OPTIONS':
        return '', 200

    data = request.get_json()
    keywords = data.get("keywords", "")

    # ✅ 키워드 JSON 파일에 저장
    with open("keywords.json", "w", encoding="utf-8") as f:
        json.dump({"keywords": keywords}, f, ensure_ascii=False, indent=4)

    return jsonify({"message": f"키워드 '{keywords}' 저장됨"}), 200


@app.route("/get_keywords", methods=["GET"])
def get_keywords():
    try:
        if os.path.exists("keywords.json"):
            with open("keywords.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                return jsonify(data)
        return jsonify({"keywords": ""})  # 키워드가 없으면 빈 값 반환
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ✅ 뉴스 검색 API (POST 요청 허용)
@app.route("/fetch_news", methods=["POST"])
def fetch_news():
    try:
        data = request.get_json()
        keywords = data.get("keywords", "").strip()

        if not keywords:
            return jsonify({"error": "키워드가 없습니다."}), 400

        keyword_list = keywords.split(",")
        all_articles = []
        today_date = datetime.now().date()  # 오늘 날짜
        recent_articles = []  # 최근 뉴스 저장용 리스트

        for keyword in keyword_list:
            articles = fetch_google_news(keyword.strip())

            # 🔹 날짜 필터링: 오늘 뉴스만 저장
            today_articles = []
            for article in articles:
                try:
                    article_date = parser.parse(article["date"]).date()
                    if article_date == today_date:
                        today_articles.append(article)
                    else:
                        recent_articles.append(article)  # 최근 뉴스 리스트에 추가
                except Exception as e:
                    print(f"❌ 날짜 변환 오류: {e}")

            all_articles.extend(today_articles)

        # 🔹 오늘 뉴스가 하나도 없으면 최근 뉴스 중 상위 5개 제공
        if not all_articles:
            print("📢 오늘 뉴스 없음 → 최근 뉴스 제공")
            all_articles = recent_articles[:5]

        return jsonify({"news": all_articles[:5]})  # 🔹 최신 뉴스 5개 반환

    except Exception as e:
        print(f"❌ 뉴스 API 오류: {e}")
        return jsonify({"error": str(e)}), 500

# ✅ 일정 목록 가져오기 (GET)
@app.route("/events", methods=["GET"])
def get_events():
    return jsonify(load_events()), 200

# ✅ 일정 추가 (POST)
@app.route("/events", methods=["POST"])
def add_event():
    events = load_events()
    new_event = request.json

    if "title" not in new_event or "date" not in new_event or "time" not in new_event:
        return jsonify({"error": "필수 데이터(title, date, time)가 누락되었습니다."}), 400

    new_event["id"] = len(events) + 1
    new_event["type"] = new_event.get("type", "이벤트")
    events.append(new_event)

    save_events(events)
    thread = Thread(target=schedule_notification, args=(new_event,))
    thread.start()

    return jsonify(new_event), 201

# ✅ 일정 수정 (PUT)
@app.route("/events/<int:event_id>", methods=["PUT"])
def update_event(event_id):
    events = load_events()
    updated_event = request.json

    for event in events:
        if event["id"] == event_id:
            event.update(updated_event)
            event["pre_notified"] = False
            save_events(events)
            thread = Thread(target=schedule_notification, args=(event,))
            thread.start()
            return jsonify(event), 200

    return jsonify({"error": "일정을 찾을 수 없음"}), 404

# ✅ 일정 삭제 (DELETE)
@app.route("/events/<int:event_id>", methods=["DELETE"])
def delete_event(event_id):
    events = [event for event in load_events() if event["id"] != event_id]
    save_events(events)
    return jsonify({"message": "일정 삭제 완료"}), 200

# ✅ 일정 알림 스레드 실행 함수
def schedule_notification(event):
    event_id = event["id"]
    event_time_str = f"{event['date']} {event['time']}"
    event_time = datetime.strptime(event_time_str, "%Y-%m-%d %H:%M")
    now = datetime.now()

    # ✅ 과거 일정이면 알림을 보내지 않음
    if now > event_time:
        print(f"🚫 과거 일정 ({event['date']}) - 알림을 보내지 않음")
        return  

    # ✅ 기존 알림 로직 유지 (미리 알림 포함)
    pre_alert_time = None
    if event["importance"] == "긴급":
        pre_alert_time = event_time - timedelta(hours=24)
    elif event["importance"] == "보통":
        pre_alert_time = event_time - timedelta(hours=5)

    if pre_alert_time and now < pre_alert_time:
        time.sleep((pre_alert_time - now).total_seconds())
        socketio.emit("event_reminder", {
            "id": event_id, "title": event["title"], "date": event["date"],
            "time": event["time"], "type": "미리 알림"
        })

    # ✅ 당일 알림 실행 (과거 일정 필터링됨)
    time.sleep((event_time - datetime.now()).total_seconds())
    socketio.emit("event_reminder", {
        "id": event_id,
        "title": event["title"],
        "date": event["date"],
        "time": event["time"],
        "type": "당일 알림"
    })

    event["day_notified"] = True
    save_events(load_events())

# ✅ 서버 시작 시 기존 일정 알림 예약
def initialize_notifications():
    events = load_events()
    for event in events:
        thread = Thread(target=schedule_notification, args=(event,))
        thread.start()

# ✅ Flask-SocketIO 서버 실행
if __name__ == "__main__":
    initialize_notifications()
    socketio.run(app, debug=True, host="0.0.0.0", port=5000)
