import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.newsletter import PreferencesUpdateRequest, SubscribeRequest
from app.infrastructure.repositories.subscriber_repo import SubscriberRepository
from app.services.subscriber_service import SubscriberService


@pytest.mark.asyncio
async def test_subscriber_create_and_duplicate(db_session: AsyncSession):
    """Test idempotent subscriber creation and reactivation."""
    service = SubscriberService(session=db_session)
    
    test_email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    req = SubscribeRequest(
        email=test_email,
        name="Alex Doe",
        age_group="25-34",
        frequency="DAILY",
        preferred_channel="EMAIL",
        topics=["AI Agents", "RAG"],
    )
    
    # 1. Initial creation
    subscriber, is_created = await service.subscribe(req)
    assert is_created is True
    assert subscriber.email == test_email.lower()
    assert subscriber.name == "Alex Doe"
    assert subscriber.is_active is True
    assert subscriber.unsubscribe_token is not None
    assert subscriber.topics == ["AI Agents", "RAG"]

    # 2. Duplicate subscription with updated name/topics
    req2 = SubscribeRequest(
        email=test_email,
        name="Alexander Doe",
        topics=["LLMs", "Research"],
    )
    subscriber2, is_created2 = await service.subscribe(req2)
    assert is_created2 is False
    assert subscriber2.id == subscriber.id
    assert subscriber2.name == "Alexander Doe"
    assert subscriber2.topics == ["LLMs", "Research"]
    assert subscriber2.is_active is True


@pytest.mark.asyncio
async def test_subscriber_unsubscribe_and_reactivation(db_session: AsyncSession):
    """Test tokenized unsubscribe and subsequent re-subscription."""
    service = SubscriberService(session=db_session)
    test_email = f"unsub_{uuid.uuid4().hex[:8]}@example.com"
    
    sub, _ = await service.subscribe(SubscribeRequest(email=test_email))
    token = sub.unsubscribe_token
    
    # Unsubscribe via token
    success = await service.unsubscribe(token=token)
    assert success is True
    
    repo = SubscriberRepository(db_session)
    fetched = await repo.get_by_email(test_email)
    assert fetched is not None
    assert fetched.is_active is False
    assert fetched.unsubscribed_at is not None

    # Unsubscribe with invalid token returns False
    invalid_success = await service.unsubscribe(token="non_existent_token_xyz")
    assert invalid_success is False

    # Reactivate by subscribing again
    reactivated, is_created = await service.subscribe(SubscribeRequest(email=test_email))
    assert is_created is False
    assert reactivated.is_active is True
    assert reactivated.unsubscribed_at is None


@pytest.mark.asyncio
async def test_subscriber_preferences_update(db_session: AsyncSession):
    """Test updating preferences for an active subscriber."""
    service = SubscriberService(session=db_session)
    test_email = f"pref_{uuid.uuid4().hex[:8]}@example.com"
    
    await service.subscribe(SubscribeRequest(email=test_email))
    
    update_req = PreferencesUpdateRequest(
        email=test_email,
        name="Updated Name",
        age_group="35-44",
        frequency="DAILY",
        preferred_channel="EMAIL",
        topics=["Automation", "Machine Learning"],
    )
    updated = await service.update_preferences(update_req)
    assert updated is not None
    assert updated.name == "Updated Name"
    assert updated.age_group == "35-44"
    assert updated.topics == ["Automation", "Machine Learning"]

    # Non-existent subscriber returns None
    missing = await service.update_preferences(
        PreferencesUpdateRequest(email="unknown_user_999@example.com")
    )
    assert missing is None


@pytest.mark.asyncio
async def test_newsletter_api_endpoints(async_client: AsyncClient):
    """Test the public FastAPI newsletter endpoints."""
    test_email = f"api_{uuid.uuid4().hex[:8]}@example.com"

    # 1. Subscribe
    res = await async_client.post(
        "/api/v1/newsletter/subscribe",
        json={
            "email": test_email,
            "name": "API Subscriber",
            "age_group": "18-24",
            "frequency": "DAILY",
            "topics": ["AI Agents", "Robotics"],
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["email"] == test_email
    assert data["created"] is True

    # 2. Count
    res_count = await async_client.get("/api/v1/newsletter/subscribers/count")
    assert res_count.status_code == 200
    assert res_count.json()["active_subscribers"] >= 1

    # 3. Preferences Update
    res_pref = await async_client.put(
        "/api/v1/newsletter/preferences",
        json={
            "email": test_email,
            "name": "API Subscriber Renamed",
            "topics": ["Deep Learning"],
        },
    )
    assert res_pref.status_code == 200
    assert res_pref.json()["name"] == "API Subscriber Renamed"

    # 4. Unsubscribe via POST
    res_unsub = await async_client.post(
        "/api/v1/newsletter/unsubscribe",
        json={"email": test_email},
    )
    assert res_unsub.status_code == 200
    assert res_unsub.json()["status"] == "success"

    # 5. Invalid email validation error
    res_invalid = await async_client.post(
        "/api/v1/newsletter/subscribe",
        json={"email": "not-a-valid-email-address"},
    )
    assert res_invalid.status_code == 422
