from flask import Flask, request, jsonify, send_file
import os, json, base64, traceback, tempfile
from io import BytesIO

import firebase_admin
from firebase_admin import credentials, storage, db as rtdb, firestore

from openai import OpenAI
from PIL import Image
from datetime import datetime

from werkzeug.security import generate_password_hash, check_password_hash
#-------------------------------------
import qrcode
import io
import uuid
import time
 
INSTALL_URL = "https://jai.app/install"

# ------------------- Flask ----------- 
app = Flask(__name__)

# ------------------- Firebase Config -------------------
RTD_URL1 = "https://bestofm-a31a0-default-rtdb.asia-southeast1.firebasedatabase.app/" # realtime database
BUCKET_NAME = "bestofm-a31a0.firebasestorage.app"

service_account_json = os.environ.get("FIREBASE_SERVICE_KEY")
if not service_account_json:
    raise RuntimeError("Missing FIREBASE_SERVICE_KEY")

cred = credentials.Certificate(json.loads(service_account_json))

firebase_admin.initialize_app(
    cred,
    {
        "storageBucket": BUCKET_NAME,
        "databaseURL": RTD_URL1
    }
)

db = firestore.client()
rtdb_ref = rtdb.reference("/")
bucket = storage.bucket()

# ------------ข้อมูลการทงทะเบียนผู้ดูแลระบบ สร้างตลาด fresh market
@app.route("/register_admin", methods=["POST"])
def register_admin():
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "status": "error",
                "message": "No JSON received"
            }), 400

        admin_name = data.get("adminname")
        admin_add = data.get("adminadd")
        admin_phone = data.get("adminphone")
        admin_pass = data.get("addminpass")

        # 🔹 ตรวจข้อมูลจำเป็น
        if not admin_name or not admin_phone or not admin_pass:
            return jsonify({
                "status": "error",
                "message": "ข้อมูลไม่ครบ"
            }), 400

        doc_ref = db.collection("registeradminOFM").document(admin_name)
        doc = doc_ref.get()

        # 🔹 เช็คชื่อซ้ำ
        if doc.exists:
            return jsonify({
                "status": "error",
                "message": "ชื่อผู้ดูแลซ้ำ กรุณาตั้งชื่อใหม่"
            }), 200   # ❗ ส่ง 200 เพื่อให้ MAUI อ่าน message ได้

        # 🔹 บันทึกข้อมูล
        doc_ref.set({
            "admin_name": admin_name,
            "adminadd": admin_add,
            "adminphone": admin_phone,
            "addminpass": admin_pass,  # ⚠️ แนะนำ hash ใน production
            "created_at": firestore.SERVER_TIMESTAMP
        })

        return jsonify({
            "status": "success"
        }), 200

    except Exception as e:
       # logging.exception("🔥 register_admin error")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


if __name__ == "__main__":
    app.run(debug=True)
