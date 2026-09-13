from typing import List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.logging import logger
from app.domain.models.newsletter import PreferencesUpdateRequest, SubscribeRequest
from app.infrastructure.models.subscriber import Subscriber
from app.infrastructure.repositories.subscriber_repo import SubscriberRepository


class SubscriberService:
    """Service orchestrating newsletter subscriber lifecycle, subscriptions, and preferences."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = SubscriberRepository(session)

    async def subscribe(self, request: SubscribeRequest) -> Tuple[Subscriber, bool]:
        """
        Subscribe or reactivate a user.
        Returns: (subscriber, is_created)
        """
        clean_email = str(request.email).strip().lower()
        subscriber, is_created = await self.repo.upsert_subscriber(
            email=clean_email,
            name=request.name,
            age_group=request.age_group,
            frequency=request.frequency or "DAILY",
            preferred_channel=request.preferred_channel or "EMAIL",
            topics=request.topics or [],
        )
        await self.session.commit()
        logger.info(
            f"Subscriber processed: email={clean_email}, created={is_created}, active={subscriber.is_active}"
        )
        return subscriber, is_created

    async def unsubscribe(self, token: Optional[str] = None, email: Optional[str] = None) -> bool:
        """
        Deactivate a subscriber by token (preferred) or email.
        Returns True if a subscriber was deactivated, False otherwise.
        """
        success = False
        if token:
            sub = await self.repo.deactivate_by_token(token)
            if sub:
                logger.info(f"Subscriber unsubscribed via token: email={sub.email}")
                success = True
        elif email:
            sub = await self.repo.deactivate_by_email(str(email).strip().lower())
            if sub:
                logger.info(f"Subscriber unsubscribed via email: email={sub.email}")
                success = True

        if success:
            await self.session.commit()
        return success

    async def update_preferences(self, request: PreferencesUpdateRequest) -> Optional[Subscriber]:
        """Update preferences for an existing subscriber by email."""
        clean_email = str(request.email).strip().lower()
        existing = await self.repo.get_by_email(clean_email)
        if not existing:
            return None

        updated = await self.repo.update_preferences(
            subscriber_id=existing.id,
            name=request.name,
            age_group=request.age_group,
            frequency=request.frequency,
            preferred_channel=request.preferred_channel,
            topics=request.topics,
        )
        await self.session.commit()
        logger.info(f"Subscriber preferences updated: email={clean_email}")
        return updated

    async def get_active_subscribers(self, frequency: Optional[str] = "DAILY") -> List[Subscriber]:
        """Retrieve all active subscribers scheduled for delivery."""
        return await self.repo.list_active(frequency=frequency)

    async def get_active_count(self) -> int:
        """Count active subscribers."""
        return await self.repo.count_active()
