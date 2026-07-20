"""AndroidWorld FastAPI server with compact observation endpoints."""

from __future__ import annotations

import dataclasses
import io
from typing import Annotated

from android_world.env import adb_utils
from fastapi import Body, Depends
from fastapi.responses import Response
from PIL import Image
from server.android_server import app, get_app_android_env


@app.get('/pi/screenshot')
def pi_screenshot(
    wait_to_stabilize: bool = False,
    env=Depends(get_app_android_env),
) -> Response:
  state = env.get_state(wait_to_stabilize=wait_to_stabilize)
  stream = io.BytesIO()
  Image.fromarray(state.pixels).save(stream, format='PNG')
  return Response(content=stream.getvalue(), media_type='image/png')


@app.get('/pi/state')
def pi_state(
    wait_to_stabilize: bool = False,
    env=Depends(get_app_android_env),
) -> dict:
  state = env.get_state(wait_to_stabilize=wait_to_stabilize)
  height, width = state.pixels.shape[:2]
  elements = []
  for index, element in enumerate(state.ui_elements):
    value = dataclasses.asdict(element)
    bbox = element.bbox_pixels or element.bbox
    if bbox is None:
      continue
    value.update({
        'index': index,
        'bounds': f'[{bbox.x_min},{bbox.y_min}][{bbox.x_max},{bbox.y_max}]',
        'center': {
            'x': round((bbox.x_min + bbox.x_max) / 2),
            'y': round((bbox.y_min + bbox.y_max) / 2),
        },
    })
    elements.append(value)
  return {'width': width, 'height': height, 'elements': elements}


@app.post('/pi/swipe')
def pi_swipe(
    x1: Annotated[int, Body()],
    y1: Annotated[int, Body()],
    x2: Annotated[int, Body()],
    y2: Annotated[int, Body()],
    duration_ms: Annotated[int, Body()] = 400,
  env=Depends(get_app_android_env),
) -> dict[str, str]:
  command = adb_utils.generate_swipe_command(x1, y1, x2, y2, duration_ms)
  adb_utils.issue_generic_request(command, env.controller)
  return {'status': 'success'}
