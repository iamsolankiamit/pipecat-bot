"""
Handler functions for appointment booking flow
These functions are called by the conversation flow and interact with the NestJS API
"""

from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional
from loguru import logger


# Helper function to calculate hours until appointment
def calculate_hours_until(scheduled_time: str) -> float:
    """Calculate hours from now until the scheduled time"""
    appointment_time = datetime.fromisoformat(scheduled_time.replace('Z', '+00:00'))
    now = datetime.now(appointment_time.tzinfo)
    diff = appointment_time - now
    return diff.total_seconds() / 3600


# Node 1: Greeting handlers
async def handle_new_appointment(args: Dict, bot) -> Tuple[Dict, str]:
    """Customer wants to schedule a new appointment"""
    logger.info("Customer wants to schedule new appointment")
    bot.state["intent"] = "new_appointment"
    return {}, "service_type"


async def handle_reschedule(args: Dict, bot) -> Tuple[Dict, str]:
    """Customer wants to reschedule existing appointment"""
    logger.info(f"Customer wants to reschedule appointment: {args.get('confirmation_number')}")
    bot.state["intent"] = "reschedule"
    bot.state["confirmation_number"] = args.get("confirmation_number")
    return {}, "reschedule_policy_check"


async def handle_cancel(args: Dict, bot) -> Tuple[Dict, str]:
    """Customer wants to cancel appointment"""
    logger.info(f"Customer wants to cancel appointment: {args.get('confirmation_number')}")
    bot.state["intent"] = "cancel"
    bot.state["confirmation_number"] = args.get("confirmation_number")
    return {}, "cancel_policy_check"


# Node 2: Service type handler
async def set_service_type(args: Dict, bot) -> Tuple[Dict, str]:
    """Save service type and optional issue description"""
    service_type = args["service_type"]
    issue_description = args.get("issue_description")

    logger.info(f"Service type set: {service_type}")

    bot.state["service_type"] = service_type
    if issue_description:
        bot.state["issue_description"] = issue_description

    return {"service_type": service_type}, "check_availability"


# Node 3: Calendar check handler
async def check_calendar(args: Dict, bot) -> Tuple[Dict, str]:
    """Check calendar availability via NestJS API"""
    preferred_date = args["preferred_date"]
    service_type = bot.state.get("service_type")

    logger.info(f"Checking availability for {preferred_date}")

    try:
        # Call NestJS API to check availability
        result = await bot.api_client.check_availability(
            date=preferred_date,
            duration_hours=2,
            service_type=service_type
        )

        if result and result.get("available"):
            slots = result.get("slots", [])
            logger.info(f"Found {len(slots)} available slots")

            bot.state["available_slots"] = slots
            bot.state["preferred_date"] = preferred_date

            # Select first available slot by default
            if slots:
                bot.state["selected_slot"] = slots[0]
                return {"found_slots": True, "num_slots": len(slots)}, "collect_contact_info"
            else:
                return {"found_slots": False}, "check_availability"
        else:
            logger.warning(f"No availability found for {preferred_date}")
            return {"found_slots": False}, "check_availability"

    except Exception as e:
        logger.error(f"Error checking calendar: {e}")
        return {"error": str(e)}, "check_availability"


# Node 4: Contact info handler
async def save_contact_info(args: Dict, bot) -> Tuple[Dict, str]:
    """Save customer contact information"""
    name = args["name"]
    phone = args.get("phone", bot.caller_phone)
    email = args.get("email")

    logger.info(f"Saving contact info: {name}, {phone}")

    bot.state["customer_name"] = name
    bot.state["customer_phone"] = phone
    if email:
        bot.state["customer_email"] = email

    return {"name": name}, "confirm_booking"


# Node 5: Confirmation handlers
async def confirm_and_book(args: Dict, bot) -> Tuple[Dict, str]:
    """Create the appointment via NestJS API"""
    logger.info("Customer confirmed - creating appointment")

    try:
        # Prepare appointment data
        selected_slot = bot.state.get("selected_slot")
        if not selected_slot:
            logger.error("No time slot selected")
            return {"error": "No time slot selected"}, "check_availability"

        appointment_data = {
            "customerPhone": bot.state.get("customer_phone", bot.caller_phone),
            "customerName": bot.state.get("customer_name"),
            "customerEmail": bot.state.get("customer_email"),
            "scheduledTime": selected_slot["start"],
            "endTime": selected_slot["end"],
            "serviceType": bot.state["service_type"],
            "issueDescription": bot.state.get("issue_description"),
            "callId": bot.call_sid
        }

        # Create appointment
        appointment = await bot.api_client.create_appointment(appointment_data)

        if appointment:
            logger.info(f"Appointment created: {appointment['confirmationNumber']}")

            bot.state["appointment"] = appointment
            bot.state["confirmation_number"] = appointment["confirmationNumber"]
            bot.state["outcome"] = "BOOKED"

            return {
                "booked": True,
                "confirmation_number": appointment["confirmationNumber"]
            }, "appointment_confirmed"
        else:
            logger.error("Failed to create appointment")
            return {"booked": False}, "check_availability"

    except Exception as e:
        logger.error(f"Error creating appointment: {e}")
        return {"error": str(e)}, "check_availability"


async def handle_modification(args: Dict, bot) -> Tuple[Dict, str]:
    """Customer wants to change something"""
    what_to_change = args.get("what_to_change", "")
    logger.info(f"Customer wants to modify: {what_to_change}")

    # For now, just go back to availability check
    # In a more advanced flow, we could intelligently route based on what they want to change
    return {"modified": True}, "check_availability"


# Node 6: End call handler
async def end_call(args: Dict, bot) -> Tuple[Dict, str]:
    """End the conversation"""
    logger.info("Ending call")
    return {}, None  # None transition ends the flow


# Reschedule handlers
async def check_reschedule_policy(args: Dict, bot) -> Tuple[Dict, str]:
    """Check if appointment can be rescheduled and any fees"""
    confirmation_number = args.get("confirmation_number") or bot.state.get("confirmation_number")

    if not confirmation_number:
        logger.error("No confirmation number provided")
        return {"error": "No confirmation number"}, "greeting"

    logger.info(f"Checking reschedule policy for {confirmation_number}")

    try:
        # Look up appointment
        appointment = await bot.api_client.get_appointment_by_confirmation(confirmation_number)

        if not appointment:
            logger.warning(f"Appointment not found: {confirmation_number}")
            return {"found": False}, "greeting"

        # Calculate hours until appointment
        hours_until = calculate_hours_until(appointment["scheduledTime"])

        bot.state["appointment_id"] = appointment["id"]
        bot.state["appointment"] = appointment
        bot.state["within_24h"] = hours_until < 24

        logger.info(f"Appointment found. Within 24h: {hours_until < 24}")

        return {
            "within_24h": hours_until < 24,
            "hours_until": hours_until,
            "current_time": appointment["scheduledTime"]
        }, "execute_reschedule"

    except Exception as e:
        logger.error(f"Error checking reschedule policy: {e}")
        return {"error": str(e)}, "greeting"


async def reschedule_appointment(args: Dict, bot) -> Tuple[Dict, str]:
    """Reschedule appointment to new time"""
    new_date = args["new_date"]
    new_time = args.get("new_time", "09:00")

    appointment_id = bot.state.get("appointment_id")
    if not appointment_id:
        return {"error": "No appointment to reschedule"}, "greeting"

    logger.info(f"Rescheduling appointment {appointment_id} to {new_date} {new_time}")

    try:
        # Parse new date/time
        new_datetime = datetime.strptime(f"{new_date} {new_time}", "%Y-%m-%d %H:%M")
        end_datetime = new_datetime + timedelta(hours=2)

        # Update appointment
        updated = await bot.api_client.update_appointment(
            appointment_id,
            {
                "scheduledTime": new_datetime.isoformat(),
                "endTime": end_datetime.isoformat()
            }
        )

        if updated:
            logger.info(f"Appointment rescheduled successfully")
            bot.state["appointment"] = updated
            bot.state["confirmation_number"] = updated["confirmationNumber"]
            bot.state["outcome"] = "RESCHEDULED"

            return {"rescheduled": True}, "appointment_confirmed"
        else:
            return {"rescheduled": False}, "execute_reschedule"

    except Exception as e:
        logger.error(f"Error rescheduling appointment: {e}")
        return {"error": str(e)}, "execute_reschedule"


# Cancel handlers
async def cancel_appointment_handler(args: Dict, bot) -> Tuple[Dict, str]:
    """Cancel the appointment"""
    confirmation_number = args.get("confirmation_number") or bot.state.get("confirmation_number")

    if not confirmation_number:
        return {"error": "No confirmation number"}, "greeting"

    logger.info(f"Cancelling appointment {confirmation_number}")

    try:
        # Look up appointment first
        appointment = await bot.api_client.get_appointment_by_confirmation(confirmation_number)

        if not appointment:
            return {"cancelled": False, "error": "Appointment not found"}, "greeting"

        # Cancel it
        result = await bot.api_client.cancel_appointment(appointment["id"])

        if result:
            logger.info(f"Appointment cancelled successfully")
            bot.state["appointment"] = result
            bot.state["outcome"] = "CANCELLED"

            return {"cancelled": True}, "appointment_confirmed"
        else:
            return {"cancelled": False}, "cancel_policy_check"

    except Exception as e:
        logger.error(f"Error cancelling appointment: {e}")
        return {"error": str(e)}, "cancel_policy_check"


async def keep_appointment(args: Dict, bot) -> Tuple[Dict, str]:
    """Customer decides to keep their appointment"""
    logger.info("Customer decided to keep appointment")
    bot.state["outcome"] = "NO_CHANGE"
    return {"kept": True}, "appointment_confirmed"
