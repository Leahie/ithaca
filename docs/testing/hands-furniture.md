# Testing guide: arranging furniture with your hands

For testers. Hands look around the tour, and arrange furniture while arranging is open. The phone
still works alongside them.
Code: `apps/web/src/components/tabs/TourTab.tsx` (search for "Hands look around" and "Hands arrange furniture").

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

The view stays where it is. Raise one hand.  A ring appears on the big screen and follows your palm: that is your cursor.
Blue ring and **L** for your left hand, coral and **R** for your right.

## Looking around

This is what hands do in the normal tour view.

| Move | What to do | What you should see |
|---|---|---|
| **Look** | Pinch and move your hand. | Inside, the room follows your hand, as when you drag with the mouse. Outside or from above, the camera circles the house. |
| **Zoom** | Pinch with both hands, then pull them apart or bring them together. | Apart zooms in, together zooms out. |
| **Turn** | Open hand, a quick sideways sweep. | The view turns on the spot, the way your hand went. |
| **Step** | Point with the index finger (the others curled), or make a fist. | One step forward, or back, to the next scan point. It repeats while you hold the pose. |

The hint under the camera box says which of these it sees.

## Arranging

Hands arrange furniture instead of looking around while one of these is true:

- **Arrange** is open on the big screen;
- the phone is on its **Furniture** screen;
- a layout is on show.

The big screen then looks down, from close, on the part of the room the camera was standing in.
Close **Arrange** (and leave Furniture on the phone) to go back to looking around.

### The four moves

| Move | What to do | What you should see |
|---|---|---|
| **Point at a piece** | Open hand, move the ring near a piece. | That piece turns blue. It stays blue until another piece is clearly nearer. |
| **Grab** | With the piece blue, touch thumb tip to index fingertip and hold. Keep the other fingers relaxed, not in a fist. | The ring tightens. The hint under the camera box reads "Move your hand · let go to place". |
| **Move** | Keep pinching and move your hand. | The piece slides over the floor and follows your hand, slightly behind it. |
| **Turn** | Still holding with the first hand, pinch with the second hand too, then turn both hands like a steering wheel. | The piece rotates on the spot. Open the second hand to stop turning; the first still holds it. |
| **Keep working on it** | After placing, the piece stays blue: it is the chosen one. Pinch anywhere to pick it up again; your hand does not have to be on it. To choose another piece, pinch right on that one. | The chosen piece stays blue while your open hand moves over others. |
| **Place** | Open the first hand. | The piece settles at once and is saved. The phone's plan shows it in the new place. |

## Things to try, and what counts as a pass

1. **Grab the right one.** Two pieces close together: point at one, then the other. The blue
   should not flicker between them while your hand is still. Pass: you can pick either on purpose.
2. **Hold on.** Grab a piece and move it slowly across the room for five seconds. Pass: it never
   drops by itself.
3. **Quick release.** Open your hand. Pass: it stops following at once (within about a tenth of a second).
4. **Turn a quarter.** Grab, add the second hand, turn a quarter turn. Pass: the piece turns
   about a quarter, the same way as your hands. If it turns the opposite way, report it.
5. **Out of the room.** Drag a piece past a wall into nothing and let go. Pass: it goes back to
   where it was.
6. **Through a door.** Drag a piece into the next room and let go. Pass: it stays there.
7. **Lost hand.** While holding a piece, take your hand out of the camera's view. Pass: the piece
   stays where it was last and is saved there, not thrown somewhere else.
8. **Phone still works.** With hands on, walk to another room with the phone. Pass: the view
   moves as usual and you can grab pieces in the new room.
9. **Look, then arrange.** With hands on and Arrange closed, pinch-drag and zoom. Pass: the view
   moves and no piece is picked up. Open Arrange. Pass: the screen looks down and a pinch grabs.

## Known limits

- You cannot bring a new piece into the house by hand; add it from the phone.
- Pieces cannot be resized.

## When reporting a problem

Say which of these it was, since each has its own setting (`HAND_HOLD`, `HAND_GRAB_RADIUS`):

- it **dropped** the piece while you were still pinching;
- it was **slow to let go**;
- the piece **lagged** behind the hand, or **shook**;
- it grabbed the **wrong piece**, or none;
- it **turned the wrong way**.

Also say whether the coloured lines in the camera box were on your hand at that moment. If they
were gone, the camera lost the hand, and lighting or distance is the first thing to change.
