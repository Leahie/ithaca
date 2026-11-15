# Testing guide: arranging furniture with your hands

For testers. Hands only arrange furniture; the phone still does all walking and looking.
Code: `apps/web/src/components/tabs/TourTab.tsx` (search for "Hands arrange furniture").

## Before you start

1. `apps/web/.env.local` has `hands` in `NEXT_PUBLIC_FEATURES`.
2. Open the house on the laptop in Chrome. Allow the camera when asked.
3. The family on screen has at least two pieces placed in one room (place them from the phone's
   furniture screen if the house is empty), and the camera is standing in that room.
4. Sit or stand 0.5 to 2 m from the laptop camera, with your hands lit from the front. A bright
   window behind you makes tracking drop out.

## Turn it on

Press **Hands off** at the top right of the big screen (or **Hands** in the controls bar). You
should see, within a few seconds:

- the pill reads **Hands on**;
- a small camera box at the bottom left showing you, with coloured lines drawn on your hands.

The view stays where it is, standing in the room. For the room from above, press the cube button
on the phone's furniture screen; hands work in both views, and from above is easier for long moves.

Raise one hand. A ring appears on the big screen and follows your palm: that is your cursor.
Blue ring and **L** for your left hand, coral and **R** for your right.

## The four moves

| Move | What to do | What you should see |
|---|---|---|
| **Point at a piece** | Open hand, move the ring near a piece. | That piece turns blue. It stays blue until another piece is clearly nearer. |
| **Grab** | With the piece blue, touch thumb tip to index fingertip and hold. Keep the other fingers relaxed, not in a fist. | The ring tightens. The hint under the camera box reads "Move your hand · let go to place". |
| **Move** | Keep pinching and move your hand. | The piece slides over the floor and follows your hand, slightly behind it. |
| **Turn** | Still holding with the first hand, pinch with the second hand too, then turn both hands like a steering wheel. | The piece rotates on the spot. Open the second hand to stop turning; the first still holds it. |
| **Place** | Open the first hand. | After about half a second the piece settles and is saved. The phone's plan shows it in the new place. |

## Things to try, and what counts as a pass

1. **Grab the right one.** Two pieces close together: point at one, then the other. The blue
   should not flicker between them while your hand is still. Pass: you can pick either on purpose.
2. **Hold on.** Grab a piece and move it slowly across the room for five seconds. Pass: it never
   drops by itself.
3. **Quick release.** Open your hand. Pass: it stops following within about half a second.
4. **Turn a quarter.** Grab, add the second hand, turn a quarter turn. Pass: the piece turns
   about a quarter, the same way as your hands. If it turns the opposite way, report it.
5. **Out of the room.** Drag a piece past a wall into nothing and let go. Pass: it goes back to
   where it was.
6. **Through a door.** Drag a piece into the next room and let go. Pass: it stays there.
7. **Lost hand.** While holding a piece, take your hand out of the camera's view. Pass: the piece
   stays where it was last and is saved there, not thrown somewhere else.
8. **Phone still works.** With hands on, walk to another room with the phone. Pass: the view
   moves as usual and you can grab pieces in the new room.

## Known limits

- You cannot bring a new piece into the house by hand; add it from the phone.
- Pieces cannot be resized.
- Hands do not move the camera in the tour.

## When reporting a problem

Say which of these it was, since each has its own setting (`HAND_HOLD`, `HAND_GRAB_RADIUS`):

- it **dropped** the piece while you were still pinching;
- it was **slow to let go**;
- the piece **lagged** behind the hand, or **shook**;
- it grabbed the **wrong piece**, or none;
- it **turned the wrong way**.

Also say whether the coloured lines in the camera box were on your hand at that moment. If they
were gone, the camera lost the hand, and lighting or distance is the first thing to change.
