"""
API client for World of Doors NestJS backend
"""

import os
from typing import Optional, Dict, List
import aiohttp
from loguru import logger


class WorldOfDoorsAPIClient:
    """Client for interacting with the NestJS API"""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or os.getenv("NESTJS_API_URL", "http://localhost:3000")
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict]:
        """Make an HTTP request to the API"""
        if not self.session:
            self.session = aiohttp.ClientSession()

        url = f"{self.base_url}{endpoint}"

        # Log the API call
        logger.info(f"📡 API CALL | {method} {endpoint}")
        if kwargs.get('json'):
            logger.debug(f"📤 REQUEST DATA | {kwargs['json']}")

        try:
            async with self.session.request(method, url, **kwargs) as response:
                if response.status in [200, 201]:
                    # Check content type before parsing JSON
                    content_type = response.headers.get('content-type', '')
                    if 'application/json' in content_type:
                        data = await response.json()
                        logger.info(f"✅ API SUCCESS | {method} {endpoint} - Status {response.status}")
                        logger.debug(f"📥 RESPONSE DATA | {data}")
                        # Handle null responses (e.g., contact not found)
                        return data if data is not None else None
                    else:
                        text = await response.text()
                        logger.warning(f"⚠️  Non-JSON response: {text[:100]}")
                        return None
                else:
                    error_text = await response.text()
                    logger.error(f"❌ API FAILED | {method} {url} - {response.status}: {error_text}")
                    return None

        except Exception as e:
            logger.error(f"❌ API ERROR | {method} {endpoint} - {e}")
            return None

    # Contact endpoints

    async def lookup_contact(self, phone: str) -> Optional[Dict]:
        """
        Lookup a contact by phone number

        Args:
            phone: Phone number to lookup

        Returns:
            Contact data dict or None if not found
        """
        logger.info(f"Looking up contact by phone: {phone}")
        return await self._request("GET", f"/contacts/lookup?phone={phone}")

    async def create_contact(self, data: Dict) -> Optional[Dict]:
        """
        Create a new contact

        Args:
            data: Contact data (firstName, lastName, phone, email, address)

        Returns:
            Created contact data or None
        """
        logger.info(f"Creating contact: {data.get('firstName')} {data.get('lastName')}")
        return await self._request("POST", "/contacts", json=data)

    async def get_contact(self, contact_id: str) -> Optional[Dict]:
        """Get contact by ID"""
        return await self._request("GET", f"/contacts/{contact_id}")

    # Appointment endpoints

    async def create_appointment(self, data: Dict) -> Optional[Dict]:
        """
        Create a new appointment

        Args:
            data: Appointment data
                - customerPhone (required if no contactId)
                - customerName (required if creating new contact)
                - customerEmail (optional)
                - scheduledTime (ISO 8601 string)
                - endTime (ISO 8601 string)
                - serviceType (REPAIR, INSTALLATION, MAINTENANCE, EMERGENCY)
                - issueDescription (optional)

        Returns:
            Created appointment with confirmation number or None
        """
        logger.info(f"📞 Creating appointment for {data.get('customerPhone', data.get('contactId'))}")
        logger.debug(f"📋 Appointment data: {data}")
        return await self._request("POST", "/appointments", json=data)

    async def get_appointment(self, appointment_id: str) -> Optional[Dict]:
        """Get appointment by ID"""
        return await self._request("GET", f"/appointments/{appointment_id}")

    async def get_appointment_by_confirmation(self, confirmation_number: str) -> Optional[Dict]:
        """
        Get appointment by confirmation number

        Args:
            confirmation_number: Confirmation number (e.g., WOD123456)

        Returns:
            Appointment data or None
        """
        logger.info(f"Looking up appointment: {confirmation_number}")
        return await self._request("GET", f"/appointments/by-confirmation/{confirmation_number}")

    async def update_appointment(self, appointment_id: str, data: Dict) -> Optional[Dict]:
        """
        Update/reschedule an appointment

        Args:
            appointment_id: Appointment ID
            data: Update data (scheduledTime, endTime, serviceType, status, etc.)

        Returns:
            Updated appointment data or None
        """
        logger.info(f"Updating appointment: {appointment_id}")
        return await self._request("PATCH", f"/appointments/{appointment_id}", json=data)

    async def cancel_appointment(self, appointment_id: str) -> Optional[Dict]:
        """
        Cancel an appointment

        Args:
            appointment_id: Appointment ID

        Returns:
            Cancelled appointment data or None
        """
        logger.info(f"Cancelling appointment: {appointment_id}")
        return await self._request("DELETE", f"/appointments/{appointment_id}")

    # Calendar endpoints

    async def check_availability(self, date: str, duration_hours: int = 2, service_type: Optional[str] = None) -> Optional[Dict]:
        """
        Check calendar availability

        Args:
            date: Date to check (YYYY-MM-DD format)
            duration_hours: Duration in hours (default 2)
            service_type: Service type (optional)

        Returns:
            Dict with 'available' (bool) and 'slots' (list of time slots)
        """
        logger.info(f"Checking availability for {date}")

        data = {
            "date": date,
            "durationHours": duration_hours
        }

        if service_type:
            data["serviceType"] = service_type

        return await self._request("POST", "/calendar/check-availability", json=data)

    async def get_upcoming_appointments(self) -> Optional[List[Dict]]:
        """Get all upcoming appointments"""
        return await self._request("GET", "/appointments/upcoming")

    async def close(self):
        """Close the HTTP session"""
        if self.session:
            await self.session.close()
            self.session = None
