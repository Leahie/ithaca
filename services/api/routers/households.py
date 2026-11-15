"""Households and the family QR code. Owner: B.

    POST /households                 {name}                       -> Household   (the desktop)
    GET  /households/{hid}                                        -> Household
    POST /households/{hid}/invite                                 -> Household   (a new QR code)
    POST /households/join            {invite_token, name, member_id?} -> JoinHouseholdResult (a phone)

The family QR code holds the invite token, reusable for 24 hours, unlike a single phone's pairing
token (B10). Joining makes the person a member (their own user_id, their own agent memory) and,
if the family's desktop has a house open, hands back a phone token for that session.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

from packages.schema.session import CreateHouseholdRequest, Household, JoinHouseholdRequest, JoinHouseholdResult
from services.api.realtime.households import InviteInvalid, households
from services.api.realtime.hub import Hub

router = APIRouter()


@router.post("/households")
def create_household(body: CreateHouseholdRequest, x_user_id: str = Header("demo")) -> Household:
    """The caller (the big screen's user) is the host: the family arranges their house."""
    return households().create(body.name, host_user_id=x_user_id, fresh=body.fresh)


@router.get("/households/{household_id}")
def get_household(household_id: str) -> Household:
    h = households().get(household_id)
    if h is None:
        raise HTTPException(404, "unknown household")
    return h


@router.post("/households/{household_id}/invite")
def rotate_invite(household_id: str) -> Household:
    h = households().rotate_invite(household_id)
    if h is None:
        raise HTTPException(404, "unknown household")
    return h


@router.post("/households/join")
def join_household(body: JoinHouseholdRequest, request: Request) -> JoinHouseholdResult:
    try:
        household, member = households().join(body.invite_token, body.name, body.member_id)
    except InviteInvalid as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    hub: Hub = request.app.state.hub
    sid = household.active_session_id
    if not sid or sid not in hub.rooms:
        # The desktop has no house open (or the API restarted): the phone waits and asks again.
        return JoinHouseholdResult(household=household, member=member)
    return JoinHouseholdResult(household=household, member=member, session_id=sid, client_token=hub.mint_member_token(sid, member.member_id))
