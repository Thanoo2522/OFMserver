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

        # 🔹 รหัสผ่าน: ตัวเลข 6 หลัก
        if not admin_pass.isdigit() or len(admin_pass) != 6:
            return jsonify({
                "status": "error",
                "message": "รหัสผ่านต้องเป็นตัวเลข 6 หลักเท่านั้น"
            }), 200

        doc_ref = db.collection("registeradminOFM").document(admin_name)
        doc = doc_ref.get()

        # 🔹 ชื่อซ้ำ
        if doc.exists:
            return jsonify({
                "status": "error",
                "message": "ชื่อผู้ดูแลซ้ำ กรุณาตั้งชื่อใหม่"
            }), 200

        # 🔐 เข้ารหัสรหัสผ่าน
        hashed_pass = generate_password_hash(admin_pass)

        # 🔹 บันทึก Firestore
        doc_ref.set({
            "admin_name": admin_name,
            "adminadd": admin_add,
            "adminphone": admin_phone,
            "addminpass": hashed_pass,   # ✅ เก็บแบบ hash
            "created_at": firestore.SERVER_TIMESTAMP
        })

        return jsonify({
            "status": "success"
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

#----------------- check password เพื่อเข้าหน้า singmasterpage  ----
@app.route("/master_password", methods=["POST"])
def master_password():
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "status": "error",
                "message": "No JSON received"
            }), 400

        shopname = data.get("shopname")
        password = data.get("password")

        if not shopname or not password:
            return jsonify({
                "status": "error",
                "message": "ข้อมูลไม่ครบ"
            }), 400

        # 🔹 อ่าน document จาก Firestore
        doc_ref = db.collection("registeradminOFM").document(shopname)
        doc = doc_ref.get()

        # 🔸 ไม่พบร้าน
        if not doc.exists:
            return jsonify({
                "status": "not_found"
            }), 200

        doc_data = doc.to_dict()
        saved_password = doc_data.get("addminpass")

        # 🔸 รหัสผ่านไม่ถูกต้อง
        if password != saved_password:
            return jsonify({
                "status": "wrong_password"
            }), 200

        # 🔹 ผ่าน
        return jsonify({
            "status": "success",
            "adminadd": doc_data.get("adminadd", "")
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
#----------------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
