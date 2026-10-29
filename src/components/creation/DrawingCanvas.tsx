"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { Point, Stroke } from "@/lib/contracts";
import {
  captureSize,
  CANVAS_BRUSH_WIDTH,
  observedPointerPoint,
  observedTimeMs,
  newObservedStroke,
  type CaptureSize,
  type ObservedStroke,
} from "@/lib/stroke-capture";

type Props = {
  strokes: Stroke[];
  onChange: (strokes: Stroke[]) => void;
  onObservedStroke?: (
    stroke: ObservedStroke,
    size: CaptureSize,
  ) => boolean | void;
  label: string;
  height?: React.CSSProperties["height"];
  readOnly?: boolean;
  sourceSize?: CaptureSize | null;
};

export function DrawingCanvas({
  strokes,
  onChange,
  onObservedStroke,
  label,
  height = 290,
  readOnly = false,
  sourceSize = null,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const active = useRef<Stroke | null>(null);
  const observed = useRef<ObservedStroke | null>(null);
  const artboard = useRef<CaptureSize | null>(null);
  const pointerId = useRef<number | null>(null);
  const inputMode = useRef<"pointer" | "keyboard" | null>(null);
  const cursor = useRef<Point>({ x: 0.5, y: 0.5 });
  const latest = useRef({ strokes, onChange, onObservedStroke });
  latest.current = { strokes, onChange, onObservedStroke };
  const [live, setLive] = useState<Stroke | null>(null);
  const [keyboardPoint, setKeyboardPoint] = useState<Point | null>(null);
  const helpId = useId();

  const render = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(rect.width * dpr));
    const canvasHeight = Math.max(1, Math.round(rect.height * dpr));
    if (canvas.width !== width || canvas.height !== canvasHeight) {
      canvas.width = width;
      canvas.height = canvasHeight;
    }
    const context = canvas.getContext("2d");
    if (!context) return;
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, width, canvasHeight);
    context.setTransform(
      width / rect.width,
      0,
      0,
      canvasHeight / rect.height,
      0,
      0,
    );
    context.strokeStyle = "#29475f";
    context.lineWidth =
      CANVAS_BRUSH_WIDTH * (sourceSize ? rect.width / sourceSize.width : 1);
    context.fillStyle = context.strokeStyle;
    context.lineCap = "round";
    context.lineJoin = "round";
    for (const stroke of [...strokes, ...(live ? [live] : [])]) {
      if (!stroke.length) continue;
      context.beginPath();
      context.moveTo(stroke[0].x * rect.width, stroke[0].y * rect.height);
      for (const point of stroke.slice(1))
        context.lineTo(point.x * rect.width, point.y * rect.height);
      if (
        stroke.every(
          (point) => point.x === stroke[0].x && point.y === stroke[0].y,
        )
      ) {
        context.arc(
          stroke[0].x * rect.width,
          stroke[0].y * rect.height,
          context.lineWidth / 2,
          0,
          Math.PI * 2,
        );
        context.fill();
      } else context.stroke();
    }
    if (keyboardPoint && !readOnly) {
      context.beginPath();
      context.setLineDash([3, 3]);
      context.lineWidth = 1.5;
      context.arc(
        keyboardPoint.x * rect.width,
        keyboardPoint.y * rect.height,
        7,
        0,
        Math.PI * 2,
      );
      context.stroke();
      context.setLineDash([]);
    }
    context.setTransform(1, 0, 0, 1, 0, 0);
  }, [strokes, live, keyboardPoint, readOnly, sourceSize]);

  useEffect(() => {
    render();
    const observer = new ResizeObserver(render);
    if (canvasRef.current) observer.observe(canvasRef.current);
    return () => observer.disconnect();
  }, [render]);

  function point(event: React.PointerEvent<HTMLCanvasElement>): Point {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.max(
        0,
        Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)),
      ),
      y: Math.max(
        0,
        Math.min(1, (event.clientY - rect.top) / Math.max(1, rect.height)),
      ),
    };
  }
  function finish(commit: boolean) {
    const completed = active.current;
    const captured = observed.current;
    const size = artboard.current;
    active.current = null;
    observed.current = null;
    artboard.current = null;
    pointerId.current = null;
    inputMode.current = null;
    setLive(null);
    if (commit && completed?.length) {
      if (latest.current.onObservedStroke) {
        if (!captured || !size) return;
        if (latest.current.onObservedStroke(captured, size) === false) return;
      }
      latest.current.onChange([...latest.current.strokes, completed]);
    }
  }
  function start(event: React.PointerEvent<HTMLCanvasElement>) {
    if (readOnly || !event.isPrimary || event.button !== 0 || active.current)
      return;
    event.preventDefault();
    event.currentTarget.focus();
    event.currentTarget.setPointerCapture(event.pointerId);
    pointerId.current = event.pointerId;
    inputMode.current = "pointer";
    active.current = [point(event)];
    if (latest.current.onObservedStroke) {
      const rect = event.currentTarget.getBoundingClientRect();
      artboard.current = captureSize(rect);
      observed.current = newObservedStroke(
        event.pointerType,
        observedPointerPoint(event, rect, performance.timeOrigin),
      );
    }
    setKeyboardPoint(null);
    setLive([...active.current]);
  }
  function move(event: React.PointerEvent<HTMLCanvasElement>) {
    if (
      pointerId.current !== event.pointerId ||
      inputMode.current !== "pointer" ||
      !active.current
    )
      return;
    active.current.push(point(event));
    observed.current?.points.push(
      observedPointerPoint(
        event,
        event.currentTarget.getBoundingClientRect(),
        performance.timeOrigin,
      ),
    );
    setLive([...active.current]);
  }
  function keyboard(event: React.KeyboardEvent<HTMLCanvasElement>) {
    if (
      readOnly ||
      inputMode.current === "pointer" ||
      event.ctrlKey ||
      event.metaKey ||
      event.altKey
    )
      return;
    if (event.key === "Escape") {
      if (inputMode.current === "keyboard") {
        event.preventDefault();
        finish(false);
      }
      return;
    }
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      if (event.repeat) return;
      if (inputMode.current === "keyboard") finish(true);
      else {
        inputMode.current = "keyboard";
        active.current = [{ ...cursor.current }];
        if (latest.current.onObservedStroke) {
          artboard.current = captureSize(
            event.currentTarget.getBoundingClientRect(),
          );
          observed.current = newObservedStroke("keyboard", {
            ...cursor.current,
            t_ms: observedTimeMs(event.timeStamp, performance.timeOrigin),
          });
        }
        setLive([...active.current]);
      }
      setKeyboardPoint({ ...cursor.current });
      return;
    }
    if (
      !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)
    )
      return;
    event.preventDefault();
    const amount = event.shiftKey ? 0.05 : 0.015;
    cursor.current = {
      x: Math.max(
        0,
        Math.min(
          1,
          cursor.current.x +
            (event.key === "ArrowLeft"
              ? -amount
              : event.key === "ArrowRight"
                ? amount
                : 0),
        ),
      ),
      y: Math.max(
        0,
        Math.min(
          1,
          cursor.current.y +
            (event.key === "ArrowUp"
              ? -amount
              : event.key === "ArrowDown"
                ? amount
                : 0),
        ),
      ),
    };
    setKeyboardPoint({ ...cursor.current });
    if (inputMode.current === "keyboard" && active.current) {
      active.current.push({ ...cursor.current });
      observed.current?.points.push({
        ...cursor.current,
        t_ms: observedTimeMs(event.timeStamp, performance.timeOrigin),
      });
      setLive([...active.current]);
    }
  }

  return (
    <>
      <canvas
        ref={canvasRef}
        className="drawing-canvas"
        style={{
          height: sourceSize ? "auto" : height,
          aspectRatio: sourceSize
            ? `${sourceSize.width} / ${sourceSize.height}`
            : undefined,
          touchAction: readOnly ? "auto" : "none",
          cursor: readOnly ? "default" : "crosshair",
        }}
        role={readOnly ? "img" : undefined}
        tabIndex={readOnly ? undefined : 0}
        aria-label={label}
        aria-describedby={readOnly ? undefined : helpId}
        onPointerDown={start}
        onPointerMove={move}
        onPointerUp={(event) => {
          if (pointerId.current === event.pointerId && active.current) {
            active.current.push(point(event));
            observed.current?.points.push(
              observedPointerPoint(
                event,
                event.currentTarget.getBoundingClientRect(),
                performance.timeOrigin,
              ),
            );
            finish(true);
          }
        }}
        onPointerCancel={(event) => {
          if (pointerId.current === event.pointerId) finish(false);
        }}
        onLostPointerCapture={(event) => {
          if (pointerId.current === event.pointerId) finish(true);
        }}
        onKeyDown={keyboard}
        onBlur={() => {
          if (inputMode.current === "keyboard") finish(true);
          setKeyboardPoint(null);
        }}
      >
        Your browser does not support canvas. Use an uploaded image or the demo
        sample instead.
      </canvas>
      {!readOnly && (
        <span className="sr-only" id={helpId}>
          Draw with a mouse, finger, or pen. With a keyboard, use arrow keys to
          position the cursor, press Space or Enter to start a stroke, use arrow
          keys to draw, then press Space or Enter to finish. Shift moves
          farther. Escape cancels the current stroke.
        </span>
      )}
    </>
  );
}
