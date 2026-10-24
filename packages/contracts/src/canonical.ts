/** penforge-tagged-json-ieee754-v1; shared fixtures pin Python/JS parity. */
export type JsonValue =
  null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

const utf8Hex = (value: string): string => {
  // TextEncoder replaces lone surrogates, which would destroy source identity.
  for (const character of value) {
    const point = character.codePointAt(0)!;
    if (point >= 0xd800 && point <= 0xdfff)
      throw new Error("invalid_unicode_scalar");
  }
  return Array.from(new TextEncoder().encode(value), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
};

/** Inspect data properties without evaluating accessors or dropping symbol keys. */
function objectEntries(value: JsonValue): [string, JsonValue][] {
  if (
    value === null ||
    typeof value !== "object" ||
    Object.getPrototypeOf(value) !== Object.prototype
  )
    throw new Error("unsupported_json_value");
  return Reflect.ownKeys(value).map((key) => {
    if (typeof key !== "string") throw new Error("unsupported_json_property");
    const descriptor = Object.getOwnPropertyDescriptor(value, key)!;
    if (!descriptor.enumerable || !("value" in descriptor))
      throw new Error("unsupported_json_property");
    return [key, descriptor.value as JsonValue];
  });
}

function encode(
  value: JsonValue,
  excludeKeys: readonly string[] = [],
): JsonValue {
  if (value === null) return ["null"];
  if (typeof value === "boolean") return ["bool", value];
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("nonfinite_number");
    if (Number.isInteger(value) && !Number.isSafeInteger(value))
      throw new Error("unsafe_integer");
    const buffer = new ArrayBuffer(8);
    new DataView(buffer).setFloat64(0, value === 0 ? 0 : value, false);
    const hex = Array.from(new Uint8Array(buffer), (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");
    return ["number", hex];
  }
  if (typeof value === "string") return ["string", utf8Hex(value)];
  if (Array.isArray(value)) {
    if (
      Object.getPrototypeOf(value) !== Array.prototype ||
      Reflect.ownKeys(value).length !== value.length + 1
    )
      throw new Error("unsupported_json_array");
    const items: JsonValue[] = [];
    for (let index = 0; index < value.length; index++) {
      const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
      if (!descriptor || !descriptor.enumerable || !("value" in descriptor))
        throw new Error("sparse_or_accessor_array");
      items.push(encode(descriptor.value));
    }
    return ["array", items];
  }
  const entries = objectEntries(value)
    // Validate all source values before exclusion; omitted hash fields do not
    // authorize non-JSON objects, undefined, accessors or symbols.
    .map(([key, item]) => [key, encode(item)] as [string, JsonValue])
    .filter(([key]) => !excludeKeys.includes(key))
    .map(([key, item]) => [utf8Hex(key), item] as [string, JsonValue])
    .sort(([first], [second]) =>
      first < second ? -1 : first > second ? 1 : 0,
    );
  return ["object", entries];
}

export function canonicalBytes(
  value: JsonValue,
  excludeRootKeys: readonly string[] = [],
): Uint8Array {
  if (excludeRootKeys.length) {
    if (value === null || typeof value !== "object" || Array.isArray(value))
      throw new Error("root_exclusion_requires_object");
  }
  return new TextEncoder().encode(
    JSON.stringify(encode(value, excludeRootKeys)),
  );
}

export async function contentHash(
  value: JsonValue,
  excludeRootKeys: readonly string[] = [],
): Promise<string> {
  const bytes = canonicalBytes(value, excludeRootKeys);
  const digest = await crypto.subtle.digest(
    "SHA-256",
    bytes as Uint8Array<ArrayBuffer>,
  );
  return `sha256:${Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("")}`;
}
