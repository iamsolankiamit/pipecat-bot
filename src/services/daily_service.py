"""
Daily.co service for managing WebRTC rooms
"""

import time
import aiohttp
from typing import Optional, Dict
from loguru import logger


class DailyService:
    """Service for interacting with Daily.co API"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.base_url = "https://api.daily.co/v1"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        } if api_key else {}

    async def create_room(
        self,
        name: str,
        expire_time: int = 3600,
        enable_recording: bool = True
    ) -> Optional[Dict]:
        """
        Create a Daily room for a voice call

        Args:
            name: Room name
            expire_time: Room expiration time in seconds (default 1 hour)
            enable_recording: Whether to enable call recording

        Returns:
            Room data dict with url, name, and sip_uri
        """
        if not self.api_key:
            logger.warning("Daily API key not configured, using mock room")
            return self._create_mock_room(name)

        try:
            exp = int(time.time()) + expire_time

            data = {
                "name": name,
                "properties": {
                    "exp": exp,
                    "enable_recording": "cloud" if enable_recording else "disabled",
                    "start_audio_off": False,
                    "start_video_off": True,
                    "sip": {
                        "display_name": "World of Doors Bot",
                        "video": False,
                        "sip_mode": "dial-in",
                        "num_endpoints": 1
                    }
                }
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/rooms",
                    headers=self.headers,
                    json=data
                ) as response:
                    if response.status == 200:
                        room_data = await response.json()
                        logger.info(f"Created Daily room: {room_data['name']}")

                        # Get SIP endpoint - should be provided by Daily when SIP is enabled
                        sip_endpoint = room_data.get("sip_uri")

                        if not sip_endpoint:
                            logger.error(f"No sip_uri in room response. SIP may not be enabled for account. Room data: {room_data.keys()}")
                            # Try constructing as fallback
                            sip_endpoint = f"sip:{room_data['name']}@sip.daily.co"
                            logger.warning(f"Using fallback SIP URI: {sip_endpoint}")
                        else:
                            logger.info(f"✓ SIP endpoint from Daily.co: {sip_endpoint}")

                        return {
                            "name": room_data["name"],
                            "url": room_data["url"],
                            "sip_uri": sip_endpoint,
                            "sip_endpoint": sip_endpoint
                        }
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to create room: {error_text}")
                        return None

        except Exception as e:
            logger.error(f"Error creating Daily room: {e}")
            return None

    async def delete_room(self, room_name: str) -> bool:
        """
        Delete a Daily room

        Args:
            room_name: Name of the room to delete

        Returns:
            True if successful, False otherwise
        """
        if not self.api_key:
            logger.info(f"Mock: Would delete room {room_name}")
            return True

        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{self.base_url}/rooms/{room_name}",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        logger.info(f"Deleted Daily room: {room_name}")
                        return True
                    else:
                        logger.warning(f"Failed to delete room {room_name}: {response.status}")
                        return False

        except Exception as e:
            logger.error(f"Error deleting Daily room: {e}")
            return False

    async def get_room(self, room_name: str) -> Optional[Dict]:
        """
        Get room information

        Args:
            room_name: Name of the room

        Returns:
            Room data dict or None
        """
        if not self.api_key:
            return self._create_mock_room(room_name)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/rooms/{room_name}",
                    headers=self.headers
                ) as response:
                    if response.status == 200:
                        room_data = await response.json()
                        return {
                            "name": room_data["name"],
                            "url": room_data["url"],
                            "sip_uri": room_data.get("sip_endpoint", "")
                        }
                    return None

        except Exception as e:
            logger.error(f"Error getting room info: {e}")
            return None

    def _create_mock_room(self, name: str) -> Dict:
        """Create a mock room for development without Daily API key"""
        return {
            "name": name,
            "url": f"https://mock.daily.co/{name}",
            "sip_uri": f"sip:mock-{name}@sip.daily.co"
        }
