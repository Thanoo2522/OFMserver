from itertools import product
from flask import Flask, request, jsonify,Response , stream_with_context
import os, json, io, traceback
import requests
from io import BytesIO
from PIL import Image
import firebase_admin
from firebase_admin import credentials, storage, db as rtdb, firestore, messaging

from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import time
 
 

 
# ------------------------------------
# Flask
# ------------------------------------
app = Flask(__name__)

# ------------------------------------
# Firebase Config
# ------------------------------------
RTD_URL1 = "https://bestofm-a31a0-default-rtdb.asia-southeast1.firebasedatabase.app/"
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

# ✅ ใช้ Firebase Admin เท่านั้น (ไม่มี ADC)
db = firestore.client()
rtdb_ref = rtdb.reference("/")
bucket = storage.bucket()

# ------------------------------------
# Utils
# ------------------------------------
    #-----ฟังก์ชันส่ง FCM (Backend) Firebase Cloud Messaging (FCM) แจ้งร้าน
def send_fcm_to_partner(fcm_token, title, body, data=None):
    try:
        if not fcm_token:
            return

        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body
            ),
            data=data or {},
            token=fcm_token
        )

        messaging.send(message)

    except Exception as e:
        print("❌ FCM error:", e)
        #------------------------------
def build_prefixes(text: str):
    text = text.lower().strip()
    prefixes = []
    current = ""
    for ch in text:
        current += ch
        prefixes.append(current)
    return prefixes


@firestore.transactional
def update_qty(transaction, ref, delta):
    snap = ref.get(transaction=transaction)
    qty = snap.get("numberproduct")
    transaction.update(ref, {"numberproduct": max(qty + delta, 1)})




    #-----------โหลด หมวดทั้งหมด
@app.route("/warehouse/modes", methods=["GET"])
def get_warehouse_modes():
    prefix = "warehouseMode/"
    modes = set()

    for blob in bucket.list_blobs(prefix=prefix):
        parts = blob.name.split("/")
        if len(parts) > 1 and parts[1]:
            modes.add(parts[1])

    return jsonify(sorted(list(modes)))

   #-----------โหลดรูปทั้งหมด
from datetime import timedelta

@app.route("/warehouse/images/<path:mode>", methods=["GET"])
def get_warehouse_images_by_mode(mode):
    prefix = f"warehouseMode/{mode}/"
    images = []

    for blob in bucket.list_blobs(prefix=prefix):
        if blob.name.lower().endswith((".jpg", ".png", ".jpeg")):
            url = blob.generate_signed_url(
                version="v4",
                expiration=timedelta(hours=1),
                method="GET"
            )

            filename = os.path.basename(blob.name)  # ชื่อไฟล์
            name_only = os.path.splitext(filename)[0]  # ตัด .jpg

            images.append({
                "imageUrl": url,
                "imageName": name_only
            })

    return jsonify(images)

#---
@app.route("/lab/step2")
def step2():
    docs = db.collection_group("product") \
             .where("productname", "==", "สามแม่ครัว") \
                .where("slave_name", "==", "seafood") \
             .stream()
    return jsonify([d.id for d in docs])



# --- ดึงหมวดสินค้า
@app.route("/get_modes/<name_ofm>", methods=["GET"])
def get_modes_by_ofm(name_ofm):
    modes = []

    docs = (
        db.collection("OFM_name")
          .document(name_ofm)
          .collection("modproduct")
          .stream()
    )

    for d in docs:
        modes.append(d.id)  # ใช้ชื่อ document เป็นชื่อหมวด

    return jsonify(modes)


#---ดึงร้านค้า
# --- ดึงร้านค้า
 

#---ดึงสินค้า
@app.route("/get_shops_by_mode/<nameOfm>/<mode>", methods=["GET"])
def get_shops_by_mode(nameOfm, mode):
    shops = []

    partners = (
        db.collection("OFM_name")
          .document(nameOfm)
          .collection("partner")
          .stream()
    )

    for p in partners:
        mode_ref = (
            db.collection("OFM_name")
              .document(nameOfm)
              .collection("partner")
              .document(p.id)
              .collection("mode")
              .document(mode)
        )

        if mode_ref.get().exists:
            shops.append(p.id)

    return jsonify(shops)

# --- ดึงสินค้า
@app.route("/get_products/<name_ofm>/<slave_name>/<view_modename>", methods=["GET"])
def get_products_by_mode(name_ofm, slave_name, view_modename):
    products = []

    docs = (
        db.collection("OFM_name")
          .document(name_ofm)
          .collection("partner")
          .document(slave_name)
          .collection("mode")
          .document(view_modename)
          .collection("product")
          .stream()
    )

    for d in docs:
        data = d.to_dict() or {}
        products.append({
            "ProductName": d.id,
            "ProductDetail": data.get("dataproduct", ""),  # ✅ แก้ตรงนี้
            "Price": data.get("priceproduct", 0),
            "imageurl": data.get("image_url", ""),
        })

    return jsonify(products)



#-------------------------------------
@app.route("/get_preorder", methods=["GET"])
def get_preorder():
    nameOfm = request.args.get("nameOfm")
    userName = request.args.get("userName")

    if not nameOfm or not userName:
        return jsonify({
            "status": "error",
            "message": "Missing nameOfm or userName"
        }), 400

    customer_ref = (
        db.collection("OFM_name")
          .document(nameOfm)
          .collection("customers")
          .document(userName)
    )

    customer_doc = customer_ref.get()

    # 1️⃣ ถ้ายังไม่มี customer → สร้าง
    if not customer_doc.exists:
        customer_ref.set({
            "activeOrderId": "",
            "createdAt": datetime.utcnow()
        }, merge=True)

        customer_doc = customer_ref.get()

    customer_data = customer_doc.to_dict() or {}
    active_order_id = customer_data.get("activeOrderId", "")

    # 2️⃣ ตรวจว่าต้องสร้าง order ใหม่ไหม
    need_new_order = False

    if active_order_id == "":
        need_new_order = True
    else:
        check_ref = (
            customer_ref
              .collection("orders")
              .document(active_order_id)
        )
        if not check_ref.get().exists:
            need_new_order = True

    # 3️⃣ สร้าง order ใหม่
    if need_new_order:
        timestamp_id = str(int(time.time() * 1000))

        order_ref = (
            customer_ref
              .collection("orders")
              .document(timestamp_id)
        )

        order_ref.set({
            "status": "draft",
            "Preorder": 0,
            "createdAt": datetime.utcnow()
        })

        customer_ref.update({
            "activeOrderId": timestamp_id
        })

        active_order_id = timestamp_id

    # 4️⃣ อ่าน Preorder
    order_ref = (
        customer_ref
          .collection("orders")
          .document(active_order_id)
    )

    order_doc = order_ref.get()
    order_data = order_doc.to_dict() or {}

    return jsonify({
        "status": "success",
        "Preorder": order_data.get("Preorder", 0),
        "orderId": active_order_id
    })

#---------------------------------------------
@app.route("/get_customer", methods=["GET"])
def get_customer():
    try:
        nameOfm = request.args.get("nameOfm")
        userName = request.args.get("userName")

        doc_ref = (
            db.collection("OFM_name")
              .document(nameOfm)
              .collection("customers")
              .document(userName)
        )

        doc = doc_ref.get()

        if not doc.exists:
            return jsonify({}), 200

        data = doc.to_dict()

        return jsonify({
            "CustomerName": data.get("username"),
            "PhoneNumber": data.get("phone"),
            "Address": data.get("address")
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

#----------------------------------------------
@app.route("/add_item_preorder", methods=["POST"])
def add_item_preorder():
    data = request.json or {}

    nameOfm = data.get("nameOfm")
    userName = data.get("userName")
    orderId = data.get("orderId")

    productname = data.get("productname")
    priceproduct = data.get("priceproduct", 0)
    image_url = data.get("image_url", "")
    ProductDetail = data.get("productDetail", "")

    Partnershop = data.get("partnershop", "")

    if not all([nameOfm, userName, orderId, productname]):
        return jsonify({"status": "error"}), 400

    order_ref = (
        db.collection("OFM_name")
          .document(nameOfm)
          .collection("customers")
          .document(userName)
          .collection("orders")
          .document(orderId)
    )

    # ✅ 1. สร้าง document ก่อน
    item_ref = order_ref.collection("items").document()
    itemId = item_ref.id   # 👈 ItemID ที่ Firestore สร้างให้

    # ✅ 2. บันทึกข้อมูล
    item_ref.set({
        "itemId": itemId,              # (ใส่หรือไม่ใส่ก็ได้ แต่แนะนำ)
        "productname": productname,
        "ProductDetail": ProductDetail,
        "priceproduct": priceproduct,
        "image_url": image_url,
        "Partnershop": Partnershop,
        "numberproduct": 1,
        "status": "draft",
        "ofmname": nameOfm,
        "username": userName,
        "created_at": datetime.utcnow()
    })

    # ✅ 3. update preorder count
    order_doc = order_ref.get()
    preorder = order_doc.to_dict().get("Preorder", 0) if order_doc.exists else 0
    order_ref.update({"Preorder": preorder + 1})

    # ✅ 4. ส่ง itemId กลับ
    return jsonify({
        "status": "success",
        "orderId": orderId,
        "itemId": itemId
    })
#-----------------------API สำหรับเช็ค notification ใหม่
@app.route("/partner_notifications", methods=["POST"])
def partner_notifications():
    try:
        data = request.json or {}

        nameOfm = data.get("nameOfm")
        partnershop = data.get("partnershop")
        orderId = data.get("orderId")   # optional

        if not nameOfm or not partnershop:
            return jsonify({"success": False, "error": "missing parameters"})

        orders_ref = (
            db.collection("OFM_name")
              .document(nameOfm)
              .collection("partner")
              .document(partnershop)
              .collection("system")
              .document("notification")
              .collection("orders")
        )

        # ==================================================
        # ✅ 1) MARK READ
        # ==================================================
        if orderId:
            orders_ref.document(orderId).update({
                "read": True,
                "readAt": firestore.SERVER_TIMESTAMP
            })
            return jsonify({"success": True})

        # ==================================================
        # 📥 2) LOAD NOTIFICATIONS
        # ==================================================
        docs = (
            orders_ref
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .stream()
        )

        result = []
        for d in docs:
            n = d.to_dict()

            result.append({
                "id": d.id,                                # สำหรับ _seenNotificationIds
                "orderId": n.get("orderId"),
                "customerName": n.get("userName"),
                "status": "read" if n.get("read") else "unread",
                "createdAt": n.get("createdAt")
            })

        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False})
  #---------------------------------------

@app.route("/get_active_delivery", methods=["GET"])
def get_active_delivery():
    try:
        nameOfm = request.args.get("nameOfm")
        if not nameOfm:
            return jsonify({"error": "missing nameOfm"}), 400

        col_ref = (
            db.collection("OFM_name")
              .document(nameOfm)
              .collection("delivery")
        )

        docs = col_ref.where("status", "==", "active").stream()

        riders = []
        for d in docs:
            data = d.to_dict() or {}
            riders.append({
                "deluserName": d.id,
                "del_name": data.get("del_name", ""),
                "pricedelivery": data.get("pricedelivery", 0)
            })

        return jsonify({
            "success": True,
            "riders": riders
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    #-------------------------------------
@app.route("/update_delivery_price", methods=["POST"])
def update_delivery_price():
    try:
        data = request.get_json(force=True)

        nameOfm = data.get("nameOfm")
        deluserName = data.get("deluserName")
        pricedelivery = data.get("pricedelivery")

        if not nameOfm or not deluserName or pricedelivery is None:
            return jsonify({"error": "missing params"}), 400

        # 🔹 path: OFM_name/{nameOfm}/delivery/{deluserName}
        del_ref = (
            db.collection("OFM_name")
              .document(nameOfm)
              .collection("delivery")
              .document(deluserName)
        )

        del_ref.update({
            "pricedelivery": pricedelivery
        })

        return jsonify({
            "success": True,
            "pricedelivery": pricedelivery
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

#------------------------------------------ /OFM_name/ตลาดสดมารวย/delivery/gorider
@app.route("/get_delivery_user", methods=["GET"])
def get_delivery_user():
    try:
        # -------------------------------
        # 1) รับ query params
        # -------------------------------
        nameOfm = request.args.get("nameOfm")
        deluserName = request.args.get("deluserName")

        if not nameOfm or not deluserName:
            return jsonify({
                "success": False,
                "error": "missing params"
            }), 400

        # -------------------------------
        # 2) ดึงข้อมูล rider จาก Firestore
        # path ตัวอย่าง:
        # ofm/{nameOfm}/delivery_users/{deluserName}
        # -------------------------------
        doc_ref = (
            db.collection("OFM_name")
              .document(nameOfm)
              .collection("delivery")
              .document(deluserName)
        )

        doc = doc_ref.get()

        if not doc.exists:
            return jsonify({
                "success": False,
                "error": "delivery user not found"
            }), 404

        data = doc.to_dict()

        # -------------------------------
        # 3) ส่งข้อมูลกลับ (จัด field ให้ตรง MAUI)
        # -------------------------------
        return jsonify({
            "success": True,
            "delivery": {
                "del_name": deluserName,
                "phone": data.get("phone", ""),
                "address": data.get("address", ""),
                "pricedelivery": data.get("pricedelivery", 0),
                "status": data.get("status", "active")
            }
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
#----------------------------------

from flask import request, jsonify
from google.cloud.firestore_v1 import FieldFilter, SERVER_TIMESTAMP

@app.route("/update_item_status", methods=["POST"])
def update_item_status():
    try:
        data = request.get_json(force=True)

        ofmname     = data.get("ofmname")
        partnershop_clicked = data.get("partnershop")
        order_id_clicked    = data.get("orderId")
        namerider   = data.get("namerider")

        if not ofmname or not partnershop_clicked or not order_id_clicked or not namerider:
            return jsonify({"error": "missing params"}), 400

        # =====================================================
        # 1️⃣ notification (ร้านที่กด ready)
        # =====================================================
        notify_ref = (
            db.collection("OFM_name")
              .document(ofmname)
              .collection("partner")
              .document(partnershop_clicked)
              .collection("system")
              .document("notification")
              .collection("orders")
              .document(order_id_clicked)
        )
        notify_ref.update({"read": True})

        # =====================================================
        # 2️⃣ update order ของ rider → ร้านนี้ ready
        # =====================================================
        delivery_order_ref = (
            db.collection("OFM_name")
              .document(ofmname)
              .collection("delivery")
              .document(namerider)
              .collection("orders")
              .document(order_id_clicked)
        )
        delivery_order_ref.update({
            f"{partnershop_clicked}.order": "ready"
        })

        # =====================================================
        # 3️⃣ ดึง orders ของ rider ที่ status == available
        # =====================================================
        orders_query = (
            db.collection("OFM_name")
              .document(ofmname)
              .collection("delivery")
              .document(namerider)
              .collection("orders")
              .where(filter=FieldFilter("status", "==", "available"))
        )

        for order_doc in orders_query.stream():
            order_data = order_doc.to_dict()
            order_id   = order_doc.id

            del_nameservice = order_data.get("del_nameservice")
            pricedelivery   = order_data.get("pricedelivery", 0)
            username        = order_data.get("username")
            totalprice      = order_data.get("totalprice", 0)

            # =================================================
            # 🔥 วนทุก partnershop ใน order
            # =================================================
            for partner_name, partner_data in order_data.items():

                if partner_name in [
                    "status",
                    "del_nameservice",
                    "pricedelivery",
                    "username",
                    "totalprice"
                ]:
                    continue

                items = partner_data.get("items", {})
                if not items:
                    continue

                # =================================================
                # 4️⃣ costservice ของร้านนี้ (transfer == no)
                # =================================================
                costservice_col = (
                    db.collection("OFM_name")
                      .document(ofmname)
                      .collection("partner")
                      .document(partner_name)
                      .collection("costservice")
                )

                cs_query = (
                    costservice_col
                    .where(filter=FieldFilter("transfer", "==", "no"))
                    .where(filter=FieldFilter("namerider", "==", namerider))
                    .limit(1)
                )

                cs_docs = list(cs_query.stream())

                # =================================================
                # 5️⃣ ไม่มี → สร้างใหม่ (auto-id)
                # =================================================
                if not cs_docs:
                    cs_ref = costservice_col.document()
                    cs_ref.set({
                        "transfer": "no",
                        "created_at": SERVER_TIMESTAMP,
                        "namerider": namerider,
                        "partnershop": partner_name
                    })
                else:
                    cs_ref = cs_docs[0].reference

                # =================================================
                # 6️⃣ บันทึก order ใต้ costservice
                # =================================================
                order_ref = cs_ref.collection("orders").document(order_id)
                order_ref.set({
                    "del_nameservice": del_nameservice,
                    "pricedelivery": pricedelivery,
                    "username": username,
                    "totalprice": totalprice
                }, merge=True)

                # =================================================
                # 7️⃣ บันทึก items
                # =================================================
                for item_id, item in items.items():
                    order_ref.collection("items").document(item_id).set({
                        "productname": item.get("productname"),
                        "ProductDetail": item.get("ProductDetail"),
                        "numberproduct": item.get("numberproduct"),
                        "priceproduct": item.get("priceproduct")
                    })

        return jsonify({
            "success": True,
            "updatedStatus": "ready"
        }), 200

    except Exception as e:
        print("🔥 update_item_status error:", e)
        return jsonify({"error": str(e)}), 500
  
#----------------------------------
@app.route("/get_partner_orders", methods=["GET"])
def get_partner_orders():
    try:
        ofmname = request.args.get("ofmname")
        partnershop = request.args.get("partnershop")

        if not ofmname or not partnershop:
            return jsonify({"error": "missing params"}), 400

        # ----------------------------------------
        # 🔹 query orders
        # ----------------------------------------
        docs = (
            db.collection_group("orders")
              .where("nameOfm", "==", ofmname)
              .where("partnershop", "==", partnershop)
              .order_by("createdAt")
              .limit(50)
              .stream()
        )

        results = []
        customer_cache = {}
        delivery_cache = {}   # 🔥 cache rider pricedelivery

        for d in docs:
            o = d.to_dict() or {}

            order_id   = d.id
            user_name  = o.get("userName", "")
            rider_name = o.get("del_nameservice", "")  # เช่น gorider

            # ----------------------------------------
            # 🔹 customer info (cache)
            # ----------------------------------------
            if user_name:
                if user_name not in customer_cache:
                    cus_ref = (
                        db.collection("OFM_name")
                          .document(ofmname)
                          .collection("customers")
                          .document(user_name)
                    )
                    cus_doc = cus_ref.get()
                    customer_cache[user_name] = cus_doc.to_dict() if cus_doc.exists else {}

                customer_data = customer_cache[user_name]
            else:
                customer_data = {}

            # ----------------------------------------
            # 🔹 pricedelivery จาก delivery/{rider}
            # ----------------------------------------
            pricedelivery = 0

            if rider_name:
                if rider_name not in delivery_cache:
                    delivery_ref = (
                        db.collection("OFM_name")
                          .document(ofmname)
                          .collection("delivery")
                          .document(rider_name)
                    )
                    delivery_doc = delivery_ref.get()

                    delivery_cache[rider_name] = (
                        delivery_doc.to_dict() if delivery_doc.exists else {}
                    )

                pricedelivery = delivery_cache[rider_name].get("pricedelivery", 0)

            # ----------------------------------------
            # 🔹 items (รองรับ MAP + ARRAY)
            # ----------------------------------------
            raw_items = o.get("items", {})
            items = []
            total_price = 0
            i = 1

            # CASE 1: items เป็น MAP
            if isinstance(raw_items, dict):
                for itemId, item in raw_items.items():
                    price = item.get("priceproduct", 0)
                    qty   = item.get("numberproduct", 0)

                    item["itemId"] = itemId
                    item["serial_order"] = i
                    item["TotalPrice"] = price * qty

                    total_price += item["TotalPrice"]
                    items.append(item)
                    i += 1

            # CASE 2: items เป็น ARRAY
            elif isinstance(raw_items, list):
                for item in raw_items:
                    price = item.get("priceproduct", 0)
                    qty   = item.get("numberproduct", 0)

                    item["serial_order"] = i
                    item["TotalPrice"] = price * qty

                    total_price += item["TotalPrice"]
                    items.append(item)
                    i += 1

            # ----------------------------------------
            # 🔹 response ต่อ 1 order
            # ----------------------------------------
            results.append({
                "orderId": order_id,
                "createdAt": o.get("createdAt"),
                "del_nameservice": rider_name,
                "pricedelivery": pricedelivery,   # 🔥 ตอนนี้จะได้ 20
                "userName": user_name,

                "customer": {
                    "username": customer_data.get("username", user_name),
                    "phone": customer_data.get("phone", ""),
                    "address": customer_data.get("address", "")
                },

                "items": items,
                "total_price": total_price
            })

        return jsonify(results), 200

    except Exception as e:
        print("ERROR get_partner_orders:", e)
        return jsonify({"error": str(e)}), 500

#--------------------------------------------
@app.route("/complete_delivery_order", methods=["POST"])
def complete_delivery_order():
    try:
        data = request.get_json(force=True)

        nameOfm     = data.get("ofmname")
        deluserName = data.get("deluserName")
        orderId     = data.get("orderId")

        # -------------------------------
        # validate
        # -------------------------------
        if not nameOfm or not deluserName or not orderId:
            return jsonify({
                "success": False,
                "error": "missing params"
            }), 400

        # -------------------------------
        # firestore path
        # -------------------------------
        order_ref = (
            db.collection("OFM_name")
              .document(nameOfm)
              .collection("delivery")
              .document(deluserName)
              .collection("orders")
              .document(orderId)
        )

        snap = order_ref.get()
        if not snap.exists:
            return jsonify({
                "success": False,
                "error": "order not found"
            }), 404

        # -------------------------------
        # update status
        # -------------------------------
        order_ref.update({
            "status": "completed"
        })

        return jsonify({
            "success": True,
            "message": "order completed"
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
#--------------------------------------
@app.route("/get_rider_orders", methods=["GET"])
def get_rider_orders():
    try:
        ofmname = request.args.get("ofmname")
        delname = request.args.get("delname")

        if not ofmname or not delname:
            return jsonify({"error": "missing params"}), 400

        orders_ref = (
            db.collection("OFM_name")
              .document(ofmname)
              .collection("delivery")
              .document(delname)
              .collection("orders")
              .where("status", "==", "available")
        )

        results = []

        for doc in orders_ref.stream():
            data = doc.to_dict()

            # ---------- customer ----------
            username = data.get("username", "")
            customer = {}

            if username:
                cust_doc = (
                    db.collection("OFM_name")
                      .document(ofmname)
                      .collection("customers")
                      .document(username)
                      .get()
                )
                if cust_doc.exists:
                    c = cust_doc.to_dict()
                    customer = {
                        "name": c.get("name", c.get("username", "")),
                        "phone": c.get("phone", ""),
                        "address": c.get("address", "")
                    }

            # ---------- items ----------
            items = []
            total_price = 0
            serial = 1

            for shop_name, shop_data in data.items():
                if not isinstance(shop_data, dict):
                    continue

                for _, product in shop_data.items():
                    if not isinstance(product, dict):
                        continue
                    if "productname" not in product:
                        continue

                    qty = int(product.get("numberproduct", 1))
                    price = float(product.get("priceproduct", 0))

                    items.append({
                        "serial_order": serial,
                        "shop": shop_name,
                        "productname": product.get("productname", ""),
                        "numberproduct": qty,
                        "priceproduct": price,
                        "ProductDetail": product.get("ProductDetail", ""),
                        "image_url": (
                                      product.get("imageurl")
                                      or product.get("image_url")
                                      or product.get("imageUrl")   
                                      )
                                      })

                    total_price += qty * price
                    serial += 1

            results.append({
                "orderId": doc.id,
                "status": data.get("status", ""),
                "username": username,
                "customer": customer,
                "pricedelivery": data.get("pricedelivery", 0),
                "total_price": total_price,
                "items": items
            })

        return jsonify({"orders": results}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500






#--------------------------------
@app.route("/final_order", methods=["POST"])
def final_order():
    ofmname = request.args.get("ofmname")
    partnershop = request.args.get("partnershop")
    orderId = request.args.get("orderId")

    ref = (
        db.collection("OFM_name")
          .document(ofmname)
          .collection("partner")
          .document(partnershop)
          .collection("system")
          .document("notification")
          .collection("orders")
          .document(orderId)
    )

    ref.update({
        "status": "read"
    })

    return jsonify({"success": True})


#---------------------------------
@app.route("/get_notifications", methods=["GET"])
def get_notifications():
    nameOfm = request.args.get("nameOfm")
    partnershop = request.args.get("partnershop")

    if not nameOfm or not partnershop:
        return jsonify({"error": "missing params"}), 400

    docs = (
        db.collection("OFM_name")
        .document(nameOfm)
        .collection("partner")
        .document(partnershop)
        .collection("system")
        .document("notification")
        .collection("orders")
        .where("read", "==", False)                    # 🔥 unread only
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
        .limit(20)                                     # 🔥 FIX LIMIT
        .stream()
    )

    result = []
    for d in docs:
        data = d.to_dict()

        created_at = data.get("createdAt")
        created_at = (
            created_at.isoformat()
            if isinstance(created_at, datetime)
            else None
        )

        result.append({
            "id": d.id,
            "orderId": str(data.get("orderId", "")),
            "customerName": data.get("userName") or "",
            "createdAt": created_at,
            "read": False
        })

    return jsonify(result)

#-----------------------------
 
 #---------------------------------
@app.route("/confirm_order", methods=["POST"])
def confirm_order():
    try:
        # ------------------------------------------------
        # 0) รับ JSON body
        # ------------------------------------------------
        data = request.get_json(force=True)

        nameOfm         = data.get("nameOfm")
        userName        = data.get("userName")
        orderId         = data.get("orderId")
        mandelivery     = data.get("mandelivery")
        pricedelivery   = data.get("pricedelivery", 0)
        del_nameservice = data.get("delman")

        if not all([nameOfm, userName, orderId]):
            return jsonify({"success": False, "error": "missing parameter"}), 400

        # ------------------------------------------------
        # 1) reference customer + order
        # ------------------------------------------------
        customer_ref = (
            db.collection("OFM_name")
              .document(nameOfm)
              .collection("customers")
              .document(userName)
        )

        order_ref = (
            customer_ref
              .collection("orders")
              .document(orderId)
        )

        if not order_ref.get().exists:
            return jsonify({"success": False, "error": "order not found"}), 404

        # ------------------------------------------------
        # 2) update order (confirm)
        # ------------------------------------------------
        order_ref.update({
            "status": "orderconfirmed",
            "Preorder": 0,
            "confirmedAt": firestore.SERVER_TIMESTAMP
        })

        # ------------------------------------------------
        # 3) clear activeOrderId
        # ------------------------------------------------
        customer_ref.update({"activeOrderId": ""})

        # ------------------------------------------------
        # 4) load items + แยกตาม Partnershop
        # ------------------------------------------------
        partner_items = {}
        total_price = 0

        for doc in order_ref.collection("items").stream():
            itemId = doc.id
            item   = doc.to_dict() or {}

            partnershop = item.get("Partnershop")
            if not partnershop:
                continue

            price = float(item.get("priceproduct", 0))
            qty   = int(item.get("numberproduct", 1))
            total_price += price * qty

            partner_items.setdefault(partnershop, {})
            partner_items[partnershop][itemId] = item

        if not partner_items:
            return jsonify({"success": False, "error": "no items"}), 400

        # ------------------------------------------------
        # 5) notification (logic เดิม)
        # ------------------------------------------------
        for partnershop, items in partner_items.items():
            (
                db.collection("OFM_name")
                  .document(nameOfm)
                  .collection("partner")
                  .document(partnershop)
                  .collection("system")
                  .document("notification")
                  .collection("orders")
                  .document(orderId)
                  .set({
                      "orderId": orderId,
                      "nameOfm": nameOfm,
                      "userName": userName,
                      "del_nameservice": del_nameservice,               
                      "partnershop": partnershop,
                      "pricedelivery":pricedelivery,
                      "items": items,
                      "read": False,
                      "createdAt": firestore.SERVER_TIMESTAMP
                  })
            )

        # ------------------------------------------------
        # 6) save to delivery/{rider}/orders/{orderId}
        # ------------------------------------------------
        call_rider_ref = (
            db.collection("OFM_name")
              .document(nameOfm)
              .collection("delivery")
              .document(del_nameservice)
              .collection("orders")
              .document(orderId)
        )

        call_rider_data = {
            "orderId": orderId,
            "username": userName,
            "pricedelivery": pricedelivery,
            "del_nameservice": del_nameservice,
            "mandelivery": mandelivery,
            "status": "available",
            "createdAt": firestore.SERVER_TIMESTAMP
        }

        # ใส่ข้อมูลร้าน + itemID (มีรายละเอียด item)
        for partnershop, items in partner_items.items():
            shop_total = 0
            shop_block = {
                "order": "available"
            }

            for itemId, item in items.items():
                price = float(item.get("priceproduct", 0))
                qty   = int(item.get("numberproduct", 1))
                shop_total += price * qty

                shop_block[itemId] = {
                    "productname": item.get("productname", ""),
                    "ProductDetail": item.get("ProductDetail", ""),
                    "priceproduct": price,
                    "numberproduct": qty,
                    "image_url":  (  item.get("imageurl")
                                      or item.get("image_url")
                                      or item.get("imageUrl")   
                                      )
                                   


                    #"prefare": "available"
                }

            shop_block["totalprice"] = shop_total
            call_rider_data[partnershop] = shop_block

        # ✅ เขียน Firestore แค่ครั้งเดียว
        call_rider_ref.set(call_rider_data)

        # ------------------------------------------------
        # 7) response
        # ------------------------------------------------
        return jsonify({
            "success": True,
            "partnerCount": len(partner_items),
            "totalprice": total_price
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500




#---------------------------------
@app.route("/mark_partner_notification_read", methods=["POST"])
def mark_partner_notification_read():
    try:
        data = request.get_json()

        nameOfm = data.get("nameOfm")
        shopname = data.get("shopname")
        orderId = data.get("orderId")

        if not nameOfm or not shopname or not orderId:
            return jsonify({
                "success": False,
                "error": "missing parameters"
            }), 400

        doc_ref = (
            db.collection("OFM_name")
              .document(nameOfm)
              .collection("partner")
              .document(shopname)
              .collection("system")
              .document("notification")
              .collection("orders")
              .document(orderId)
        )

        doc = doc_ref.get()
        if not doc.exists:
            return jsonify({
                "success": False,
                "error": "notification not found"
            }), 404

        # ✅ mark read
        doc_ref.update({
            "read": True,
            "readAt": firestore.SERVER_TIMESTAMP
        })

        return jsonify({
            "success": True,
            "orderId": orderId
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
#------------------------------------

@app.route("/get_order_items", methods=["GET"])
def get_order_items():
    nameOfm = request.args.get("nameOfm")
    userName = request.args.get("userName")
    orderId = request.args.get("orderId")

    items_ref = (
        db.collection("OFM_name").document(nameOfm)
          .collection("customers").document(userName)
          .collection("orders").document(orderId)
          .collection("items")
          .stream()
    )

    items = []
    for d in items_ref:
        data = d.to_dict()
        items.append({
            "ItemId": d.id,
            "ProductName": data.get("productname"),
            "ProductDetail": data.get("ProductDetail"),
            "Price": data.get("priceproduct"),
            "numberproduct": data.get("numberproduct"),
            "imageurl": data.get("image_url"),
            "Partnershop":data.get("Partnershop")
        })

    return jsonify(items)




#increase_item_quantity
@app.route("/increase_item_quantity", methods=["POST"])
def increase_item_quantity():
    data = request.json or {}

    nameOfm = data.get("nameOfm")
    userName = data.get("userName")
    orderId = data.get("orderId")
    itemId = data.get("itemId")

    if not all([nameOfm, userName, orderId, itemId]):
        return jsonify({"status": "error"}), 400

    item_ref = (
        db.collection("OFM_name")
          .document(nameOfm)
          .collection("customers")
          .document(userName)
          .collection("orders")
          .document(orderId)
          .collection("items")
          .document(itemId)
    )

    item_doc = item_ref.get()
    if not item_doc.exists:
        return jsonify({"status": "not_found"}), 404

    qty = item_doc.to_dict().get("numberproduct", 1)
    item_ref.update({"numberproduct": qty + 1})

    return jsonify({"status": "success"})

#decrease_item_quantity
@app.route("/decrease_item_quantity", methods=["POST"])
def decrease_item_quantity():
    data = request.json or {}

    nameOfm = data.get("nameOfm")
    userName = data.get("userName")
    orderId = data.get("orderId")
    itemId = data.get("itemId")

    if not all([nameOfm, userName, orderId, itemId]):
        return jsonify({"status": "error"}), 400

    item_ref = (
        db.collection("OFM_name")
          .document(nameOfm)
          .collection("customers")
          .document(userName)
          .collection("orders")
          .document(orderId)
          .collection("items")
          .document(itemId)
    )

    item_doc = item_ref.get()
    if not item_doc.exists:
        return jsonify({"status": "not_found"}), 404

    qty = item_doc.to_dict().get("numberproduct", 1)
    if qty > 1:
        item_ref.update({"numberproduct": qty - 1})

    return jsonify({"status": "success"})

#delete_item
@app.route("/delete_item", methods=["POST"])
def delete_item():
    data = request.json or {}

    nameOfm = data.get("nameOfm")
    userName = data.get("userName")
    orderId = data.get("orderId")
    itemId = data.get("itemId")

    if not all([nameOfm, userName, orderId, itemId]):
        return jsonify({"status": "error"}), 400

    order_ref = (
        db.collection("OFM_name")
          .document(nameOfm)
          .collection("customers")
          .document(userName)
          .collection("orders")
          .document(orderId)
    )

    item_ref = order_ref.collection("items").document(itemId)
    item_ref.delete()

    # update preorder count
    order_doc = order_ref.get()
    preorder = order_doc.to_dict().get("Preorder", 1)
    order_ref.update({"Preorder": max(preorder - 1, 0)})

    return jsonify({"status": "success"})



# Save product route
# ------------------------------
@app.route("/save_product", methods=["POST"])
def save_product():
    try:
        data = request.json

        name_ofm = data.get("name_ofm")
        slave_name = data.get("slave_name")
        view_modename = data.get("view_modename")
        view_productname = data.get("view_productname")
        dataproduct = data.get("dataproduct")
        priceproduct = data.get("priceproduct")
        preview_image_url = data.get("preview_image_url")

      

        if not all([
            name_ofm,
            slave_name,
            view_modename,
            view_productname,
            dataproduct,
            priceproduct,
            preview_image_url
        ]):
            return jsonify({"success": False, "message": "Missing fields"}), 400

        # 1) Upload image
        storage_path = f"{name_ofm}/{slave_name}/{view_modename}/{view_productname}.jpg"
        blob = bucket.blob(storage_path)

        response = requests.get(preview_image_url)
        if response.status_code != 200:
            return jsonify({
                "success": False,
                "message": "Failed to download image from MAUI"
            }), 400

        blob.upload_from_file(
            BytesIO(response.content),
            content_type="image/jpeg"
        )

        # ✅ เพิ่มแค่บรรทัดนี้
        blob.make_public()

        image_url = f"https://storage.googleapis.com/{bucket.name}/{storage_path}"

         

        # 2) Save product (logic เดิม)
        doc_ref = (
            db.collection("OFM_name")
              .document(name_ofm)
              .collection("partner")
              .document(slave_name)
              .collection("mode")
              .document(view_modename)
              .collection("product")
              .document(view_productname)
        )

        doc_ref.set({
            "name_ofm":name_ofm,
            "mode":view_modename,
            "partnershop":slave_name,
            "dataproduct":dataproduct,
            "productname":view_productname,
            "priceproduct":priceproduct,
            "image_url": image_url,
            "created_at": datetime.utcnow()
        })

        # 3) modproduct (เดิม)
        mode_ref = (
            db.collection("OFM_name")
              .document(name_ofm)
              .collection("modproduct")
              .document(view_modename)
        )

        if not mode_ref.get().exists:
            mode_ref.set({
                "view_modename": view_modename,
                "created_at": datetime.utcnow()
            })

        return jsonify({
            "success": True,
            "message": "Product saved successfully!",
            "image_url": image_url
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500

#-------------------------------------
@app.route("/load_orders", methods=["GET"])
def load_orders():
    try:
        ofmname = request.args.get("ofmname")
        username = request.args.get("username")
        order_id = request.args.get("orderId")

        if not ofmname or not username or not order_id:
            return jsonify([])
 
        items_ref = (
            db.collection("OFM_name")
              .document(ofmname)
              .collection("customers")
              .document(username)
              .collection("orders")
              .document(order_id)
              .collection("items")
        )

        docs = items_ref.stream()

        result = []
        for d in docs:
            data = d.to_dict() or {}


            result.append({
                "itemId": d.id,
                "productname": data.get("productname", ""),
                "numberproduct": data.get("numberproduct", 0),
                "into_unit": data.get("into_unit", ""),
                "priceproduct": float(data.get("priceproduct", 0)),
                "image_url": data.get("image_url", ""),
                "prepare": data.get("prepare", "Not prepared")
            })

        # (optional) เรียงตามชื่อสินค้า
        result.sort(key=lambda x: x["productname"])

        return jsonify(result)

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500

# ------------------------------------
# Admin Login
# ------------------------------------
@app.route("/ofm_password", methods=["POST"])
def ofm_password():
    try:
        data = request.get_json()
        nameofm = data.get("nameofm")
        adminpassword = data.get("adminpassword")

        if not nameofm or not adminpassword:
            return jsonify({"status": "error", "message": "ข้อมูลไม่ครบ"}), 400

        query = (
            db.collection("registeradminOFM")
            .where("nameofm", "==", nameofm)
            .limit(1)
            .stream()
        )

        admin_doc = next(query, None)
        if not admin_doc:
            return jsonify({"status": "not_found"}), 200

        doc_data = admin_doc.to_dict()
        if not check_password_hash(doc_data.get("addminpass"), adminpassword):
            return jsonify({"status": "wrong_password"}), 200

        return jsonify({
            "status": "success",
            "adminname": doc_data.get("admin_name", ""),
            "adminadd": doc_data.get("adminadd", "")
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ------------------------------------
# Register Admin + OFM
# ------------------------------------
@app.route("/register_admin_full", methods=["POST"])
def register_admin_full():
    try:
        data = request.get_json()

        nameofm = data.get("nameofm", "").strip()
        admin_name = data.get("adminname")
        admin_add = data.get("adminadd")
        admin_phone = data.get("adminphone")
        admin_pass = data.get("addminpass")

        if not nameofm or not admin_name or not admin_phone or not admin_pass:
            return jsonify({"status": "error", "message": "ข้อมูลไม่ครบ"}), 400

        # check OFM duplicate
        ofm_ref = db.collection("OFM_name").document(nameofm)
        if ofm_ref.get().exists:
            return jsonify({"status": "error", "message": "ชื่อร้านซ้ำ"}), 200

        if not admin_pass.isdigit() or len(admin_pass) != 6:
            return jsonify({"status": "error", "message": "รหัสผ่านต้อง 6 หลัก"}), 200

        ofm_ref.set({
            "OFM_name": nameofm,
            "OFM_name_lower": nameofm.lower(),
            "search_prefix": build_prefixes(nameofm),
            "created_at": firestore.SERVER_TIMESTAMP
        })

        db.collection("registeradminOFM").add({
            "nameofm": nameofm,
            "admin_name": admin_name,
            "adminadd": admin_add,
            "adminphone": admin_phone,
            "addminpass": generate_password_hash(admin_pass),
            "created_at": firestore.SERVER_TIMESTAMP
        })

        # create storage folder
        blob = bucket.blob(f"{nameofm}/.keep")
        blob.upload_from_string("")

        return jsonify({"status": "success"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ------------------------------------
# Search OFM
# ------------------------------------
@app.route("/search_adminmaster", methods=["GET"])
def search_adminmaster():
    keyword = request.args.get("q", "").lower().strip()
    if not keyword:
        return jsonify([])

    docs = (
        db.collection("OFM_name")
        .where("search_prefix", "array_contains", keyword)
        .limit(50)
        .stream()
    )

    return jsonify([{"OFM_name": d.to_dict().get("OFM_name")} for d in docs])

# ------------------------------------
 
import traceback

@app.route("/get_market_page", methods=["GET"])
def get_market_page():
    try:
        name_ofm = request.args.get("name_ofm")

        if not name_ofm:
            return jsonify({
                "success": False,
                "error": "name_ofm required"
            }), 400

        products = (
            db.collection_group("product")
              .where("name_ofm", "==", name_ofm)
              .stream()
        )

        index = {}
        modes = set()

        for p in products:
            d = p.to_dict()

            mode = d.get("mode")
            shop = d.get("partnershop")

            if not mode or not shop:
                continue

            modes.add(mode)

            index.setdefault(mode, {}).setdefault(shop, []).append({
                "productname": d.get("productname"),
                "dataproduct": d.get("dataproduct"),
                "priceproduct": d.get("priceproduct"),
                "image_url": d.get("image_url")
            })

        result = {
            "success": True,
            "modes": list(modes),
            "shops": {}
        }

        for mode in result["modes"]:
            result["shops"][mode] = index.get(mode, {})

        return jsonify(result)

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

# ------------------------------------
 

 

@app.route("/get_images", methods=["GET"])
def get_images():
    ofm = request.args.get("ofm")
    shop = request.args.get("shop")
    mode = request.args.get("mode")

    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 20))

    if not ofm or not shop or not mode:
        return jsonify({"error": "missing params"}), 400

    prefix = f"{ofm}/{shop}/{mode}/"
    images = []

    for blob in bucket.list_blobs(prefix=prefix):
        if blob.name.lower().endswith(".jpg"):
            images.append(
                f"https://storage.googleapis.com/{bucket.name}/{blob.name}"
            )

    total = len(images)
    start = (page - 1) * page_size
    end = start + page_size

    return jsonify({
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": end < total,
        "images": images[start:end]
    })
#---------------------------register_del ข้อมูลพนักงานส่ง-------
@app.route("/register_del", methods=["POST"])
def register_del():
    try:
        data = request.get_json() or {}

        # --------- รับค่าจาก MAUI ---------
        name_ofm = data.get("name_ofm", "").strip()
        del_name = data.get("delname", "").strip()
        address = data.get("address", "").strip()
        phone = data.get("phone", "").strip()
        password = data.get("password", "").strip()

        # --------- Validate ---------
        if not all([name_ofm, del_name, address, phone, password]):
            return jsonify({
                "success": False,
                "message": "ข้อมูลไม่ครบ"
            }), 400

        # --------- Firestore Path ---------
        ofm_ref = db.collection("OFM_name").document(name_ofm)
        del_ref = (
            ofm_ref
            .collection("delivery")
            .document(del_name)
        )

        # --------- Check Duplicate ---------
        if del_ref.get().exists:
            return jsonify({
                "success": False,
                "message": "ชื่อพนักงานส่งซ้ำ กรุณาใช้ชื่ออื่น"
            }), 409

        # --------- Save OFM (merge) ---------
        ofm_ref.set({
            "OFM_name": name_ofm,
            "updated_at": datetime.utcnow()
        }, merge=True)

        # --------- Save Delivery ---------
        del_ref.set({
            "del_name": del_name,
            "address": address,
            "phone": phone,
            "password_hash": generate_password_hash(password),
            "pricedelivery": 0,
            "status": "active",
            "created_at": datetime.utcnow()
        })

        return jsonify({
            "success": True,
            "message": "ลงทะเบียนพนักงานส่งสำเร็จ"
        }), 201

    except Exception as e:
        print("REGISTER DELIVERY ERROR:", str(e))
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500

#----------------------------------------------
@app.route("/register_user", methods=["POST"])
def register_customer():
    try:
        data = request.json or {}

        # --------- รับค่าจาก MAUI ---------
        name_ofm = data.get("name_ofm")
        username = data.get("username")
        address = data.get("address")
        phone = data.get("phone")
        password = data.get("password")

        # --------- Validate ---------
        if not all([name_ofm, username, address, phone, password]):
            return jsonify({
                "success": False,
                "message": "ข้อมูลไม่ครบ"
            }), 400

        # --------- Firestore Path ---------
        ofm_ref = db.collection("OFM_name").document(name_ofm)
        user_ref = (
            ofm_ref
            .collection("customers")
            .document(username)
        )

        # --------- Check Duplicate ---------
        if user_ref.get().exists:
            return jsonify({
                "success": False,
                "message": "ชื่อผู้ใช้ซ้ำ กรุณาใช้ชื่ออื่น"
            }), 409

        # --------- Save OFM (merge) ---------
        ofm_ref.set({
            "OFM_name": name_ofm,
            "updated_at": datetime.utcnow()
        }, merge=True)

        # --------- Save Customer ---------
        user_ref.set({
            "username": username,
            "address": address,
            "phone": phone,
            "password_hash": generate_password_hash(password),
            "created_at": datetime.utcnow()
        })

        return jsonify({
            "success": True,
            "message": "ลงทะเบียนลูกค้าสำเร็จ"
        }), 201

    except Exception as e:
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500

#---------------------------------------
@app.route("/register_slave", methods=["POST"])
def register_slave():
    try:
        data = request.json

        name_ofm = data.get("name_ofm")
        slavename = data.get("slavename")
        address = data.get("address")
        phone = data.get("phone")
        password = data.get("password")

        if not all([name_ofm, slavename, address, phone, password]):
            return jsonify({
                "success": False,
                "message": "ข้อมูลไม่ครบ"
            }), 400

        # ---------------- Firestore Path ----------------
        ofm_ref = db.collection("OFM_name").document(name_ofm)
        slave_ref = (
            ofm_ref
            .collection("partner")
            .document(slavename)
        )

        # ---------------- Check Duplicate ----------------
        if slave_ref.get().exists:
            return jsonify({
                "success": False,
                "message": "ชื่อร้านค้าซ้ำ กรุณาตั้งชื่อใหม่"
            }), 409

        # ---------------- Save Firestore ----------------
        ofm_ref.set({
            "OFM_name": name_ofm,
            "updated_at": datetime.utcnow()
        }, merge=True)

        slave_ref.set({
            "slavename": slavename,
            "address": address,
            "phone": phone,
            "password_hash": generate_password_hash(password),
            "created_at": datetime.utcnow()
        })

        # ---------------- Create Storage Folder ----------------
        bucket = storage.bucket()

        # สร้าง folder หลักของ OFM ถ้ายังไม่มี
        ofm_folder_blob = bucket.blob(f"{name_ofm}/.keep")
        if not ofm_folder_blob.exists():
            ofm_folder_blob.upload_from_string(
                "",
                content_type="text/plain"
            )

        # (optional) สร้าง folder ของร้านค้า slave
        slave_folder_blob = bucket.blob(f"{name_ofm}/{slavename}/.keep")
        if not slave_folder_blob.exists():
            slave_folder_blob.upload_from_string(
                "",
                content_type="text/plain"
            )

        return jsonify({
            "success": True,
            "message": "ลงทะเบียนสำเร็จ"
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500
    #------------------------------
@app.route("/del_password", methods=["POST"])
def del_password():
    try:
        data = request.get_json() or {}

        name_ofm = data.get("name_ofm", "").strip()
        del_name = data.get("del_name", "").strip()
        del_password = data.get("del_password", "").strip()

        # -------- validate --------
        if not name_ofm or not del_name or not del_password:
            return jsonify({
                "status": "error",
                "message": "missing_parameters"
            }), 400

        # -------- Firestore path --------
        # OFM_name/{name_ofm}/delivery/{del_name}
        del_ref = (
            db.collection("OFM_name")
              .document(name_ofm)
              .collection("delivery")
              .document(del_name)
        )

        doc = del_ref.get()

        # -------- not found --------
        if not doc.exists:
            return jsonify({
                "status": "not_found"
            }), 200

        del_data = doc.to_dict()
        password_hash = del_data.get("password_hash")

        # -------- no password --------
        if not password_hash:
            return jsonify({
                "status": "wrong_password"
            }), 200

        # -------- check password --------
        if not check_password_hash(password_hash, del_password):
            return jsonify({
                "status": "wrong_password"
            }), 200

        # -------- success --------
        return jsonify({
            "status": "success",
            "name_ofm": name_ofm,
            "del_name": del_name
        }), 200

    except Exception as e:
        print("DELIVERY PASSWORD ERROR:", str(e))
        return jsonify({
            "status": "server_error",
            "message": str(e)
        }), 500

  #--------------------------------
@app.route("/user_password", methods=["POST"])
def user_password():
    try:
        data = request.get_json() or {}

        name_ofm = data.get("name_ofm", "").strip()
        user_name = data.get("user_name", "").strip()
        user_password = data.get("user_password", "").strip()

        # -------- validate --------
        if not name_ofm or not user_name or not user_password:
            return jsonify({
                "status": "error",
                "message": "missing_parameters"
            }), 400

        # -------- Firestore path --------
        # OFM_name/{name_ofm}/customers/{user_name}
        user_ref = (
            db.collection("OFM_name")
              .document(name_ofm)
              .collection("customers")
              .document(user_name)
        )

        doc = user_ref.get()

        # -------- not found --------
        if not doc.exists:
            return jsonify({
                "status": "not_found"
            }), 200

        user_data = doc.to_dict()
        password_hash = user_data.get("password_hash")

        # -------- no password in db --------
        if not password_hash:
            return jsonify({
                "status": "wrong_password"
            }), 200

        # -------- check password --------
        if not check_password_hash(password_hash, user_password):
            return jsonify({
                "status": "wrong_password"
            }), 200

        # -------- success --------
        return jsonify({
            "status": "success",
            "nameofm": name_ofm,
            "username": user_name
        }), 200

    except Exception as e:
        print("USER PASSWORD ERROR:", str(e))
        return jsonify({
            "status": "server_error",
            "message": str(e)
        }), 500

#------------------------------------
@app.route("/slave_password", methods=["POST"])
def slave_password():
    try:
        data = request.get_json()

        name_ofm = data.get("name_ofm", "").strip()
        slave_name = data.get("slave_name", "").strip()
        slave_password = data.get("slave_password", "").strip()

        # 🔒 validate input
        if not name_ofm or not slave_name or not slave_password:
            return jsonify({
                "status": "error",
                "message": "missing_parameters"
            }), 400

        # 📌 Firestore path
        # OFM_name/{name_ofm}/partner/{slave_name}
        slave_ref = (
            db.collection("OFM_name")
              .document(name_ofm)
              .collection("partner")
              .document(slave_name)
        )

        doc = slave_ref.get()

        # ❌ ไม่พบร้าน
        if not doc.exists:
            return jsonify({
                "status": "not_found"
            }), 200

        slave_data = doc.to_dict()
        saved_hash = slave_data.get("password_hash")

        # ❌ ไม่มีรหัสในระบบ (กันข้อมูลพัง)
        if not saved_hash:
            return jsonify({
                "status": "wrong_password"
            }), 200

        # ❌ รหัสผ่านไม่ถูก
        if not check_password_hash(saved_hash, slave_password):
            return jsonify({
                "status": "wrong_password"
            }), 200

        # ✅ ผ่าน
        return jsonify({
            "status": "success",
            "nameofm": name_ofm,
            "slavename": slave_name
        }), 200

    except Exception as e:
        print("SLAVE PASSWORD ERROR:", str(e))
        return jsonify({
            "status": "server_error",
            "message": str(e)
        }), 500


# ------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
