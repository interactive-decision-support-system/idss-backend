"""
Supplier API endpoints for push/pull price and inventory updates.

Supports:
- Push updates (webhooks from suppliers)
- Pull updates (REST API for suppliers to query current state)
- UCP order lifecycle webhooks (per Google UCP Guide)
"""

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging
import os
import uuid

from app.database import get_db
from app.models import Product, Price, Inventory, Order
from app.cache import cache_client
from app.event_logger import log_event
from app.schemas import ResponseStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/suppliers", tags=["suppliers"])


# ============================================================================
# Request/Response Schemas
# ============================================================================

class PriceUpdateRequest(BaseModel):
    """Request to update product price."""
    product_id: str = Field(..., description="Product ID")
    price_cents: int = Field(..., description="New price in cents")
    currency: str = Field(default="USD", description="Currency code")
    supplier_id: Optional[str] = Field(None, description="Supplier identifier")
    updated_at: Optional[datetime] = Field(None, description="Update timestamp")


class InventoryUpdateRequest(BaseModel):
    """Request to update product inventory."""
    product_id: str = Field(..., description="Product ID")
    available_qty: int = Field(..., description="Available quantity")
    reserved_qty: int = Field(default=0, description="Reserved quantity")
    supplier_id: Optional[str] = Field(None, description="Supplier identifier")
    updated_at: Optional[datetime] = Field(None, description="Update timestamp")


class BulkUpdateRequest(BaseModel):
    """Bulk update request for multiple products."""
    updates: List[Dict[str, Any]] = Field(..., description="List of price/inventory updates")
    supplier_id: Optional[str] = Field(None, description="Supplier identifier")


class SupplierProductResponse(BaseModel):
    """Current product state for supplier."""
    product_id: str
    name: str
    price_cents: int
    currency: str
    available_qty: int
    reserved_qty: int
    last_updated: datetime


# ============================================================================
# Authentication (simple API key for now)
# ============================================================================

def verify_supplier_api_key(api_key: Optional[str] = Header(None, alias="X-Supplier-API-Key")):
    """
    Verify supplier API key from environment (SUPPLIER_API_KEY).
    Do not hardcode; set in .env and do not commit .env.
    """
    expected_key = os.getenv("SUPPLIER_API_KEY")
    if not expected_key or not expected_key.strip():
        raise HTTPException(status_code=503, detail="Supplier API key not configured (set SUPPLIER_API_KEY in .env)")
    if api_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid supplier API key")
    return api_key


# ============================================================================
# Push Updates (Webhooks)
# ============================================================================

@router.post("/update-price")
async def update_price(
    request: PriceUpdateRequest,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_supplier_api_key)
):
    """
    Push price update from supplier.
    
    Updates PostgreSQL and invalidates cache.
    """
    try:
        # Verify product exists
        product = db.query(Product).filter(Product.product_id == request.product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail=f"Product {request.product_id} not found")
        
        # Update price in database
        price = db.query(Price).filter(Price.product_id == request.product_id).first()
        if price:
            price.price_cents = request.price_cents
            price.currency = request.currency
            price.updated_at = request.updated_at or datetime.utcnow()
        else:
            # Create new price record
            price = Price(
                product_id=request.product_id,
                price_cents=request.price_cents,
                currency=request.currency
            )
            db.add(price)
        
        db.commit()
        
        # Invalidate cache
        cache_client.invalidate_product(request.product_id)
        
        logger.info(f"Price updated for {request.product_id}: {request.price_cents} {request.currency}")
        
        return {
            "status": "success",
            "product_id": request.product_id,
            "price_cents": request.price_cents,
            "updated_at": price.updated_at
        }
    
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating price: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update price: {str(e)}")


@router.post("/update-inventory")
async def update_inventory(
    request: InventoryUpdateRequest,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_supplier_api_key)
):
    """
    Push inventory update from supplier.
    
    Updates PostgreSQL and invalidates cache.
    """
    try:
        # Verify product exists
        product = db.query(Product).filter(Product.product_id == request.product_id).first()
        if not product:
            raise HTTPException(status_code=404, detail=f"Product {request.product_id} not found")
        
        # Update inventory in database
        inventory = db.query(Inventory).filter(Inventory.product_id == request.product_id).first()
        if inventory:
            inventory.available_qty = request.available_qty
            inventory.reserved_qty = request.reserved_qty
            inventory.updated_at = request.updated_at or datetime.utcnow()
        else:
            # Create new inventory record
            inventory = Inventory(
                product_id=request.product_id,
                available_qty=request.available_qty,
                reserved_qty=request.reserved_qty
            )
            db.add(inventory)
        
        db.commit()
        
        # Invalidate cache
        cache_client.invalidate_product(request.product_id)
        
        logger.info(f"Inventory updated for {request.product_id}: {request.available_qty} available")
        
        return {
            "status": "success",
            "product_id": request.product_id,
            "available_qty": request.available_qty,
            "updated_at": inventory.updated_at
        }
    
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating inventory: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update inventory: {str(e)}")


@router.post("/bulk-update")
async def bulk_update(
    request: BulkUpdateRequest,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_supplier_api_key)
):
    """
    Bulk update multiple products (prices and/or inventory).
    
    More efficient than individual updates for large batches.
    """
    results = []
    errors = []
    
    for update in request.updates:
        try:
            product_id = update.get("product_id")
            if not product_id:
                errors.append({"update": update, "error": "Missing product_id"})
                continue
            
            # Update price if provided
            if "price_cents" in update:
                price_req = PriceUpdateRequest(
                    product_id=product_id,
                    price_cents=update["price_cents"],
                    currency=update.get("currency", "USD")
                )
                await update_price(price_req, db, api_key)
            
            # Update inventory if provided
            if "available_qty" in update:
                inv_req = InventoryUpdateRequest(
                    product_id=product_id,
                    available_qty=update["available_qty"],
                    reserved_qty=update.get("reserved_qty", 0)
                )
                await update_inventory(inv_req, db, api_key)
            
            results.append({"product_id": product_id, "status": "success"})
        
        except Exception as e:
            errors.append({"product_id": product_id, "error": str(e)})
    
    return {
        "status": "completed",
        "successful": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors
    }


# ============================================================================
# Pull Updates (Query Current State)
# ============================================================================

@router.get("/products/{product_id}")
async def get_supplier_product(
    product_id: str,
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_supplier_api_key)
):
    """
    Get current product state for supplier.
    
    Returns current price and inventory levels.
    """
    product = db.query(Product).filter(Product.product_id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    
    price = db.query(Price).filter(Price.product_id == product_id).first()
    inventory = db.query(Inventory).filter(Inventory.product_id == product_id).first()
    
    return SupplierProductResponse(
        product_id=product.product_id,
        name=product.name,
        price_cents=price.price_cents if price else 0,
        currency=price.currency if price else "USD",
        available_qty=inventory.available_qty if inventory else 0,
        reserved_qty=inventory.reserved_qty if inventory else 0,
        last_updated=inventory.updated_at if inventory else product.updated_at
    )


# ============================================================================
# UCP Order Lifecycle Webhooks (per Google UCP Guide)
# ============================================================================

class UCPOrderEvent(BaseModel):
    """UCP order event for webhook."""
    ucp: Dict[str, Any] = Field(..., description="UCP version and capabilities")
    id: str = Field(..., description="Order ID")
    checkout_id: Optional[str] = Field(None, description="Checkout session ID")
    line_items: List[Dict[str, Any]] = Field(..., description="Order line items")
    fulfillment: Optional[Dict[str, Any]] = Field(None, description="Fulfillment details")
    permalink_url: Optional[str] = Field(None, description="Order permalink")


@router.post("/webhooks/ucp/order")
async def ucp_order_webhook(
    event: UCPOrderEvent,
    partner_id: str,
    db: Session = Depends(get_db),
    api_key: Optional[str] = Header(None, alias="X-API-Key")
):
    """
    UCP order lifecycle webhook.
    
    Per Google UCP Guide: POST /webhooks/partners/{partner_id}/events/order
    
    Triggered when:
    - Order is created (status: processing)
    - Order is updated (shipped, canceled, etc.)
    
    Must send full order entity, not partial updates.
    """
    try:
        # Log the webhook event
        request_id = str(uuid.uuid4())
        
        # Store order event in database (for replay/debugging)
        # In production, you'd also push to Google's endpoint
        
        log_event(
            db=db,
            request_id=request_id,
            tool_name="ucp_order_webhook",
            endpoint_path=f"/api/suppliers/webhooks/ucp/order",
            request_data={
                "partner_id": partner_id,
                "order_id": event.id,
                "checkout_id": event.checkout_id
            },
            response_status=ResponseStatus.OK,
            response_data={"order_id": event.id, "status": "received"},
            trace={"sources": ["webhook"], "timings_ms": {}},
            session_id=event.checkout_id
        )
        
        # Update order status in database if order exists
        order = db.query(Order).filter(Order.order_id == event.id).first()
        if order:
            # Extract status from line items
            if event.line_items:
                first_item_status = event.line_items[0].get("status", "processing")
                # Map UCP status to our order status
                if first_item_status == "fulfilled":
                    order.status = "shipped"
                elif first_item_status == "canceled":
                    order.status = "canceled"
                else:
                    order.status = "processing"
            
            db.commit()
        
        logger.info(f"UCP order webhook received: {event.id} (partner: {partner_id})")
        
        return {
            "status": "success",
            "order_id": event.id,
            "received_at": datetime.utcnow()
        }
    
    except Exception as e:
        logger.error(f"Error processing UCP order webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to process webhook: {str(e)}")
