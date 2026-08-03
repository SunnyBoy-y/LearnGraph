/**
 * Minimal DOMMatrix polyfill for jsdom.
 *
 * `pdfjs-dist` (pulled into the chat feature graph via
 * message-part-renderer → sandbox-file-artifact → file-preview →
 * document-previewers) constructs `new DOMMatrix()` at module scope, and
 * jsdom does not ship DOMMatrix. The panel tests never render PDFs, so a
 * construction-tolerant matrix with the geometry-spec methods is enough to
 * let the module graph load. Do not rely on this for real PDF rendering.
 */

function identityValues(): number[] {
  return [1, 0, 0, 1, 0, 0];
}

type MatrixLike = {
  a?: number;
  b?: number;
  c?: number;
  d?: number;
  e?: number;
  f?: number;
  m11?: number;
  m12?: number;
  m13?: number;
  m14?: number;
  m21?: number;
  m22?: number;
  m23?: number;
  m24?: number;
  m31?: number;
  m32?: number;
  m33?: number;
  m34?: number;
  m41?: number;
  m42?: number;
  m43?: number;
  m44?: number;
};

function readValues(init?: string | number[] | MatrixLike): number[] {
  const values = identityValues();
  if (typeof init === "string") {
    const parsed = init.split(/[,\s]+/u).map(Number);
    for (let index = 0; index < Math.min(values.length, parsed.length); index += 1) {
      const value = parsed[index];
      if (Number.isFinite(value)) values[index] = value;
    }
    return values;
  }
  if (Array.isArray(init)) {
    for (let index = 0; index < Math.min(values.length, init.length); index += 1) {
      const value = init[index];
      if (Number.isFinite(value)) values[index] = value;
    }
    return values;
  }
  if (init && typeof init === "object") {
    const record = init as MatrixLike;
    if (record.a !== undefined) values[0] = record.a;
    if (record.b !== undefined) values[1] = record.b;
    if (record.c !== undefined) values[2] = record.c;
    if (record.d !== undefined) values[3] = record.d;
    if (record.e !== undefined) values[4] = record.e;
    if (record.f !== undefined) values[5] = record.f;
    if (record.m11 !== undefined) values[0] = record.m11;
    if (record.m12 !== undefined) values[1] = record.m12;
    if (record.m21 !== undefined) values[2] = record.m21;
    if (record.m22 !== undefined) values[3] = record.m22;
    if (record.m41 !== undefined) values[4] = record.m41;
    if (record.m42 !== undefined) values[5] = record.m42;
  }
  return values;
}

class DOMMatrixStub {
  m11: number;
  m12: number;
  m13 = 0;
  m14 = 0;
  m21: number;
  m22: number;
  m23 = 0;
  m24 = 0;
  m31 = 0;
  m32 = 0;
  m33 = 1;
  m34 = 0;
  m41: number;
  m42: number;
  m43 = 0;
  m44 = 1;
  is2D = true;
  isIdentity: boolean;

  constructor(init?: string | number[] | MatrixLike) {
    const values = readValues(init);
    this.m11 = values[0];
    this.m12 = values[1];
    this.m21 = values[2];
    this.m22 = values[3];
    this.m41 = values[4];
    this.m42 = values[5];
    this.isIdentity =
      this.m11 === 1 &&
      this.m12 === 0 &&
      this.m21 === 0 &&
      this.m22 === 1 &&
      this.m41 === 0 &&
      this.m42 === 0;
  }

  get a(): number {
    return this.m11;
  }
  get b(): number {
    return this.m12;
  }
  get c(): number {
    return this.m21;
  }
  get d(): number {
    return this.m22;
  }
  get e(): number {
    return this.m41;
  }
  get f(): number {
    return this.m42;
  }

  set a(value: number) {
    this.m11 = value;
    this.recomputeIdentity();
  }
  set b(value: number) {
    this.m12 = value;
    this.recomputeIdentity();
  }
  set c(value: number) {
    this.m21 = value;
    this.recomputeIdentity();
  }
  set d(value: number) {
    this.m22 = value;
    this.recomputeIdentity();
  }
  set e(value: number) {
    this.m41 = value;
    this.recomputeIdentity();
  }
  set f(value: number) {
    this.m42 = value;
    this.recomputeIdentity();
  }

  private recomputeIdentity() {
    this.isIdentity =
      this.m11 === 1 &&
      this.m12 === 0 &&
      this.m21 === 0 &&
      this.m22 === 1 &&
      this.m41 === 0 &&
      this.m42 === 0;
  }

  static fromMatrix(other: MatrixLike): DOMMatrixStub {
    return new DOMMatrixStub(other);
  }
  static fromFloat32Array(values: number[]): DOMMatrixStub {
    return new DOMMatrixStub(values);
  }
  static fromFloat64Array(values: number[]): DOMMatrixStub {
    return new DOMMatrixStub(values);
  }

  multiplySelf(): this {
    return this;
  }
  preMultiplySelf(): this {
    return this;
  }
  translateSelf(): this {
    return this;
  }
  scaleSelf(): this {
    return this;
  }
  scale3dSelf(): this {
    return this;
  }
  rotateSelf(): this {
    return this;
  }
  rotateFromVectorSelf(): this {
    return this;
  }
  rotateAxisAngleSelf(): this {
    return this;
  }
  skewXSelf(): this {
    return this;
  }
  skewYSelf(): this {
    return this;
  }
  invertSelf(): this {
    return this;
  }
  setMatrixValue(): this {
    return this;
  }
  multiply(): this {
    return this;
  }
  translate(): this {
    return this;
  }
  scale(): this {
    return this;
  }
  scale3d(): this {
    return this;
  }
  rotate(): this {
    return this;
  }
  rotateFromVector(): this {
    return this;
  }
  rotateAxisAngle(): this {
    return this;
  }
  skewX(): this {
    return this;
  }
  skewY(): this {
    return this;
  }
  invert(): this {
    return this;
  }
  transformPoint(): this {
    return this;
  }
  toFloat32Array(): Float32Array {
    return new Float32Array([
      this.m11,
      this.m12,
      this.m13,
      this.m14,
      this.m21,
      this.m22,
      this.m23,
      this.m24,
      this.m31,
      this.m32,
      this.m33,
      this.m34,
      this.m41,
      this.m42,
      this.m43,
      this.m44,
    ]);
  }
  toFloat64Array(): Float64Array {
    return new Float64Array(this.toFloat32Array());
  }
}

if (typeof globalThis !== "undefined" && typeof globalThis.DOMMatrix === "undefined") {
  Object.defineProperty(globalThis, "DOMMatrix", {
    configurable: true,
    writable: true,
    value: DOMMatrixStub,
  });
}

if (typeof globalThis !== "undefined" && typeof globalThis.DOMPoint === "undefined") {
  class DOMPointStub {
    x: number;
    y: number;
    z = 0;
    w = 1;
    constructor(x = 0, y = 0, z = 0, w = 1) {
      this.x = x;
      this.y = y;
      this.z = z;
      this.w = w;
    }
  }
  Object.defineProperty(globalThis, "DOMPoint", {
    configurable: true,
    writable: true,
    value: DOMPointStub,
  });
}

export {};
