from bson import ObjectId


def object_id(value):
    if not ObjectId.is_valid(value):
        return None
    return ObjectId(value)


def user_document(
    username, email, password, role, phone, account_created,
    google_id=None, location_id="ALL", warehouse_id=None, warehouse_name=None,
    location=None, state=None, full_name=None
):
    document = {
        "username": username,
        "full_name": full_name or username,
        "email": email,
        "hashed_password": password,
        "role": role,
        "phone": phone,
        "account_created": account_created,
        "last_login": None,
        "location_id": location_id,
        "warehouse_id": warehouse_id,
        "warehouse_name": warehouse_name,
        "location": location,
        "state": state,
    }
    if google_id:
        document["google_id"] = google_id
    return document


def product_document(
    product_id,
    product_name,
    quantity,
    price,
    unit_cost=None,
    reorder_level=35,
    category_id=None,
    supplier_id=None,
    barcode_value=None,
    qr_code_value=None,
    location_id=None,
    warehouse_id=None,
    warehouse_name=None,
    location=None,
    state=None,
    product_image=None
):
    return {
        "product_id": product_id,
        "product_name": product_name,
        "quantity": quantity,
        "price": price,
        "unit_cost": unit_cost,
        "reorder_level": reorder_level,
        "category_id": category_id,
        "supplier_id": supplier_id,
        "barcode_value": barcode_value or product_id,
        "qr_code_value": qr_code_value or product_id,
        "location_id": location_id,
        "warehouse_id": warehouse_id,
        "warehouse_name": warehouse_name,
        "location": location,
        "state": state,
        "product_image": product_image,
    }


def serialize_document(document):
    if not document:
        return document

    document = dict(document)
    document["id"] = str(document.pop("_id"))
    return document


def serialize_documents(documents):
    return [serialize_document(document) for document in documents]

def category_document(
    category_id: str,
    category_name: str,
    description: str,
    role: str
):
    return {
        "category_id": category_id,
        "category_name": category_name,
        "description": description,
        "role": role
    }

def supplier_document(
    supplier_id: str,
    supplier_name: str,
    email: str,
    phone: str,
    address: str,
    role: str,
    location_id: str = None,
    warehouse_id: str = None,
    warehouse_name: str = None,
    location: str = None,
    state: str = None
):
    return {
        "supplier_id": supplier_id,
        "supplier_name": supplier_name,
        "email": email,
        "phone": phone,
        "address": address,
        "role": role,
        "location_id": location_id,
        "warehouse_id": warehouse_id,
        "warehouse_name": warehouse_name,
        "location": location,
        "state": state
    }

def inventory_history_document(
    movement_id: str,
    product_id: str,
    product_name: str,
    movement_type: str,
    quantity: int,
    previous_stock: int,
    current_stock: int,
    performed_by: str,
    role: str,
    created_at,
    note: str = None,
    location_id: str = None,
    warehouse_id: str = None,
    warehouse_name: str = None,
    location: str = None,
    state: str = None
):
    return {
        "movement_id": movement_id,
        "product_id": product_id,
        "product_name": product_name,
        "movement_type": movement_type,
        "quantity": quantity,
        "previous_stock": previous_stock,
        "current_stock": current_stock,
        "performed_by": performed_by,
        "role": role,
        "created_at": created_at,
        "note": note,
        "location_id": location_id,
        "warehouse_id": warehouse_id,
        "warehouse_name": warehouse_name,
        "location": location,
        "state": state,
    }


def purchase_document(
    purchase_id: str,
    product_id: str,
    product_name: str,
    supplier_id: str,
    quantity: int,
    unit_cost: int,
    total_cost: int,
    previous_stock: int,
    current_stock: int,
    purchased_by: str,
    role: str,
    created_at,
    note: str = None,
    **extra
):
    document = {
        "purchase_id": purchase_id,
        "product_id": product_id,
        "product_name": product_name,
        "supplier_id": supplier_id,
        "quantity": quantity,
        "unit_cost": unit_cost,
        "total_cost": total_cost,
        "previous_stock": previous_stock,
        "current_stock": current_stock,
        "purchased_by": purchased_by,
        "role": role,
        "created_at": created_at,
        "note": note
    }
    document.update(extra)
    return document


def sale_document(
    sale_id: str,
    product_id: str,
    product_name: str,
    quantity: int,
    unit_price: int,
    total_amount: int,
    previous_stock: int,
    current_stock: int,
    sold_by: str,
    role: str,
    created_at,
    customer_name: str = None,
    note: str = None,
    **extra
):
    document = {
        "sale_id": sale_id,
        "product_id": product_id,
        "product_name": product_name,
        "quantity": quantity,
        "unit_price": unit_price,
        "total_amount": total_amount,
        "previous_stock": previous_stock,
        "current_stock": current_stock,
        "sold_by": sold_by,
        "role": role,
        "created_at": created_at,
        "customer_name": customer_name,
        "note": note
    }
    document.update(extra)
    return document




