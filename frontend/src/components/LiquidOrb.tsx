import { useEffect, useRef } from 'react';
import './LiquidOrb.css';

export type LiquidOrbState = 'idle' | 'queued' | 'generating' | 'complete' | 'blocked' | 'error' | 'paused';

const VERTEX = `
attribute vec2 aPosition;
varying vec2 vPosition;
void main() {
  vPosition = aPosition;
  gl_Position = vec4(aPosition, 0.0, 1.0);
}`;

// Original procedural material: no textures, microphone input or external service.
const FRAGMENT = `
precision mediump float;
varying vec2 vPosition;
uniform float uTime;
uniform float uMood;
uniform float uSize;
float hash(vec3 p) {
  p = fract(p * 0.3183099 + vec3(0.11, 0.37, 0.73));
  p *= 17.0;
  return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}
float noise(vec3 p) {
  vec3 i = floor(p);
  vec3 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(hash(i), hash(i + vec3(1,0,0)), f.x),
                 mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
             mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x),
                 mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z);
}
float field(vec3 p) {
  return noise(p) * 0.65 + noise(p * 2.07 + 5.0) * 0.25 + noise(p * 4.11) * 0.1;
}
void main() {
  vec2 p = vPosition / 0.94;
  float radius = length(p);
  float alpha = 1.0 - smoothstep(1.0 - 3.0 / uSize, 1.0, radius);
  if (alpha < 0.001) { gl_FragColor = vec4(0); return; }
  float z = sqrt(max(0.0, 1.0 - dot(p, p)));
  vec3 surface = vec3(p, z);
  float t = uTime;
  vec3 flow = surface * 1.9 + vec3(t * 0.12, -t * 0.09, t * 0.07);
  float warp = field(flow + vec3(0, 0, t * 0.08));
  float ribbon = sin(p.x * 3.0 - p.y * 3.4 + z * 2.4 + warp * 7.0 + t * 0.22);
  float secondary = field(flow + warp * 1.8 + vec3(2.3, 4.1, 0));
  vec3 violet = vec3(0.38, 0.18, 0.86);
  vec3 cyan = vec3(0.23, 0.82, 0.86);
  vec3 coral = vec3(0.98, 0.42, 0.52);
  vec3 material = mix(violet, cyan, smoothstep(-0.65, 0.65, ribbon));
  material = mix(material, coral, smoothstep(0.49, 0.71, secondary) * 0.9);
  float diffuse = 0.53 + 0.47 * max(dot(surface, normalize(vec3(-0.6, 0.8, 1.4))), 0.0);
  float rim = pow(1.0 - z, 2.0);
  vec3 colour = material * diffuse;
  colour += vec3(0.58, 0.74, 0.98) * rim * 0.18;
  float gloss = pow(max(dot(surface, normalize(vec3(-0.36, 0.54, 1.3))), 0.0), 22.0);
  colour += vec3(0.9, 0.97, 1.0) * gloss * 0.24;
  if (uMood > 0.5 && uMood < 1.5) colour = mix(colour, vec3(0.3, 0.76, 0.64) * diffuse, 0.32);
  if (uMood > 1.5 && uMood < 2.5) colour = mix(colour, vec3(0.82, 0.3, 0.34) * diffuse, 0.58);
  if (uMood > 2.5) {
    float grey = dot(colour, vec3(0.21, 0.72, 0.07));
    colour = mix(colour, vec3(grey * 0.85, grey * 0.94, grey), 0.78);
  }
  gl_FragColor = vec4(colour, alpha);
}`;

/** Decorative activity material. Always pair with readable status text outside it. */
export function LiquidOrb({ state, className = '' }: { state: LiquidOrbState; className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let gl: WebGLRenderingContext | null;
    try {
      gl = canvas.getContext('webgl', { alpha: true, premultipliedAlpha: false, antialias: false, depth: false, stencil: false, powerPreference: 'low-power' });
    } catch { return; }
    if (!gl) return;

    const shaders: WebGLShader[] = [];
    const compile = (type: number, source: string) => {
      const shader = gl.createShader(type);
      if (!shader) return null;
      shaders.push(shader);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      return gl.getShaderParameter(shader, gl.COMPILE_STATUS) ? shader : null;
    };
    const vertex = compile(gl.VERTEX_SHADER, VERTEX);
    const fragment = compile(gl.FRAGMENT_SHADER, FRAGMENT);
    const program = vertex && fragment ? gl.createProgram() : null;
    const buffer = program ? gl.createBuffer() : null;
    const dispose = () => {
      if (buffer) gl.deleteBuffer(buffer);
      if (program) gl.deleteProgram(program);
      shaders.forEach(shader => gl.deleteShader(shader));
    };
    if (!vertex || !fragment || !program || !buffer) { dispose(); return; }
    gl.attachShader(program, vertex);
    gl.attachShader(program, fragment);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) { dispose(); return; }
    gl.useProgram(program);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(program, 'aPosition');
    if (position < 0) { dispose(); return; }
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    const timeUniform = gl.getUniformLocation(program, 'uTime');
    const moodUniform = gl.getUniformLocation(program, 'uMood');
    const sizeUniform = gl.getUniformLocation(program, 'uSize');
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const animated = state === 'idle' || state === 'queued' || state === 'generating';
    const interval = state === 'generating' ? 1000 / 24 : state === 'queued' ? 1000 / 12 : 1000 / 8;
    const speed = state === 'generating' ? 1 : state === 'queued' ? 0.65 : 0.3;
    let raf = 0;
    let alive = true;
    let contextLost = false;
    let visible = false;
    let lastFrame: number | null = null;
    let elapsed = 0;
    let size = 0;
    gl.uniform1f(moodUniform, state === 'complete' ? 1 : state === 'error' ? 2 : state === 'blocked' || state === 'paused' ? 3 : 0);

    const measure = () => {
      const width = canvas.getBoundingClientRect().width;
      const nextSize = Math.min(192, Math.max(1, Math.round(width * Math.min(window.devicePixelRatio || 1, 2))));
      if (width <= 0) return;
      if (nextSize !== size) {
        size = nextSize;
        canvas.width = size;
        canvas.height = size;
        gl.viewport(0, 0, size, size);
        gl.uniform1f(sizeUniform, size);
      }
    };
    const render = () => {
      if (!alive || contextLost || document.hidden || !visible || size === 0) return;
      gl.uniform1f(timeUniform, elapsed * speed);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      canvas.dataset.rendered = 'true';
    };
    const canAnimate = () => alive && !contextLost && visible && !document.hidden && !motion.matches && animated;
    const tick = (now: number) => {
      raf = 0;
      if (!canAnimate()) { lastFrame = null; return; }
      if (lastFrame === null || now - lastFrame >= interval) {
        if (lastFrame !== null) elapsed += Math.min(now - lastFrame, 200) / 1000;
        lastFrame = now;
        render();
      }
      raf = requestAnimationFrame(tick);
    };
    const sync = () => {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      lastFrame = null;
      if (alive && !contextLost && visible && !document.hidden) measure();
      render();
      if (canAnimate()) raf = requestAnimationFrame(tick);
    };
    const intersection = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(entries => {
      visible = entries.some(entry => entry.isIntersecting);
      sync();
    });
    if (intersection) intersection.observe(canvas);
    else {
      const rect = canvas.getBoundingClientRect();
      visible = rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < window.innerHeight;
    }
    const resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(sync);
    resize?.observe(canvas);
    const loseContext = (event: Event) => {
      event.preventDefault();
      contextLost = true;
      delete canvas.dataset.rendered;
      sync();
    };
    document.addEventListener('visibilitychange', sync);
    motion.addEventListener('change', sync);
    canvas.addEventListener('webglcontextlost', loseContext);
    sync();
    return () => {
      alive = false;
      if (raf) cancelAnimationFrame(raf);
      intersection?.disconnect();
      resize?.disconnect();
      document.removeEventListener('visibilitychange', sync);
      motion.removeEventListener('change', sync);
      canvas.removeEventListener('webglcontextlost', loseContext);
      delete canvas.dataset.rendered;
      dispose();
    };
  }, [state]);

  return <span className={`liquid-orb ${className}`.trim()} data-state={state} aria-hidden="true">
    <span className="liquid-orb__material" />
    <canvas ref={canvasRef} className="liquid-orb__canvas" width={128} height={128} />
  </span>;
}
