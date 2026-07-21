from typing import Optional

from pydantic import BaseModel, Field


class ProductCreate(BaseModel):

    product_id: str = Field(min_length=1)

    product_name: str = Field(min_length=1)

    quantity: int = Field(ge=0)

    price: int = Field(gt=0)

    unit_cost: Optional[int] = Field(None, ge=0)

    reorder_level: int = Field(35, ge=0)

    category_id: Optional[str] = Field(None, min_length=1)

    supplier_id: Optional[str] = Field(None, min_length=1)

    barcode_value: Optional[str] = Field(None, min_length=1)

    qr_code_value: Optional[str] = Field(None, min_length=1)

class CategoryCreate(BaseModel):
    category_id: str = Field(min_length=1)
    category_name: str = Field(min_length=1)
    description: str = Field(min_length=1)

class SupplierCreate(BaseModel):
    supplier_id: Optional[str] = Field(None, min_length=1)
    supplier_name: str = Field(min_length=1)
    email: str = Field(min_length=3)
    phone: str = Field(min_length=7)
    address: str = Field(min_length=1)


class StockMovementCreate(BaseModel):
    product_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    note: Optional[str] = None


class ProfileUpdate(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3)
    phone: str = Field(min_length=7, max_length=30)


class GoogleLogin(BaseModel):
    credential: str = Field(min_length=1)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3)
    recaptcha_token: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class ConfirmPasswordRequest(BaseModel):
    password: str = Field(min_length=1)


class PurchaseCreate(BaseModel):
    purchase_id: Optional[str] = Field(None, min_length=1)
    product_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    unit_cost: int = Field(gt=0)
    supplier_id: Optional[str] = Field(None, min_length=1)
    warehouse_id: Optional[str] = Field(None, min_length=1)
    transaction_id: Optional[str] = Field(None, min_length=1)
    status: Optional[str] = Field(None, min_length=1)
    purchase_date: Optional[str] = None
    note: Optional[str] = None


class RestockQueueItem(BaseModel):
    product_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    unit_cost: int = Field(gt=0)
    supplier_id: Optional[str] = Field(None, min_length=1)
    warehouse_id: Optional[str] = Field(None, min_length=1)


class RestockQueueBulkRequest(BaseModel):
    items: list[RestockQueueItem]


class RestockQueueAddAllRequest(BaseModel):
    warehouse_id: Optional[str] = Field(None, min_length=1)
    status: Optional[str] = "attention"


class RestockQueueIdentity(BaseModel):
    product_id: str = Field(min_length=1)
    warehouse_id: Optional[str] = Field(None, min_length=1)


class RestockQueueSelection(BaseModel):
    product_ids: list[str] = Field(default_factory=list)
    items: list[RestockQueueIdentity] = Field(default_factory=list)


class SaleCreate(BaseModel):
    sale_id: Optional[str] = Field(None, min_length=1)
    transaction_id: Optional[str] = Field(None, min_length=1)
    product_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    unit_price: Optional[int] = Field(None, gt=0)
    unit_cost: Optional[int] = Field(None, ge=0)
    discount_percent: int = Field(0, ge=0, le=100)
    reorder_level: int = Field(35, ge=0)
    region: Optional[str] = None
    customer_type: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None
    payment_method: Optional[str] = None
    sale_date: Optional[str] = None
    note: Optional[str] = None





