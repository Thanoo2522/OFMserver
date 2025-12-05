from flask import Flask, request, jsonify, send_file, send_from_directory
import os
import base64
from datetime import datetime
import traceback
import uuid
import json
import time
import requests
import firebase_admin
from firebase_admin import credentials, storage, db as rtdb, firestore
 
import io
from io import BytesIO

app = Flask(__name__)

# ------------------- Config -------------------get_user
RTD_URL1 = "https://retailstore-4780f-default-rtdb.asia-southeast1.firebasedatabase.app"
BUCKET_NAME = "retailstore-4780f.firebasestorage.app"
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
service_account_json = os.environ.get("FIREBASE_SERVICE_KEY")
cred = credentials.Certificate(json.loads(service_account_json))
firebase_admin.initialize_app(cred, {"storageBucket": BUCKET_NAME,"databaseURL": RTD_URL1})

db = firestore.client()
rtdb_ref = rtdb.reference("/") # ← Realtime Database root
bucket = storage.bucket()
#--------------------------------------------------------------------------------------
# 🔹 สร้าง document system/way และตั้งค่า connected="true"
@app.route("/create_connection", methods=["POST"])
def create_connection():
    try:
        doc_ref = db.collection("system").document("connection")
        doc_ref.set({
            "connected": "true"
        })
        return jsonify({
            "status": "success",
            "message": "Created document system/way with connected=true"
        }), 200
    except Exception as e:
        print("❌ Error:", e)
        return jsonify({"status": "error", "message": str(e)}), 500    
# ---------------- เช็คการเชื่อมต่อกับ firestore database ----------------
@app.route("/check_connection", methods=["GET"])
def check_connection():
    try:
        # 🔹 เข้าถึง document system/way
        doc_ref = db.collection("system").document("connection")
        doc = doc_ref.get()

        if not doc.exists:
            return jsonify({"status": "error", "message": "Document not found"}), 404

        data = doc.to_dict()
        connected = data.get("connected", "false")

        if connected == "true":
            return jsonify({"status": "success", "connected": True})
        else:
            return jsonify({"status": "success", "connected": False})

    except Exception as e:
        print("❌ Error:", e)
        return jsonify({"status": "error", "message": str(e)}), 500
#-----------------------------------------------------------