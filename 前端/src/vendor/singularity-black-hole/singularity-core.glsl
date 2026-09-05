// Hall-sized volumetric black hole.
// Rendering structure follows the approved Singularity reference:
// bent volume marching, orbiting gas noise, warm emission and a dark core.
precision highp float;

uniform vec2 u_resolution;
uniform float u_time;
uniform float u_energy;
uniform sampler2D u_noise;
out vec4 fragColor;

#define MARCH_STEPS 112
const float PI = 3.14159265359;

vec2 raySphere(vec3 ro, vec3 rd, float radius) {
    float b = dot(ro, rd);
    float c = dot(ro, ro) - radius * radius;
    float h = b * b - c;
    if (h < 0.0) return vec2(-1.0);
    h = sqrt(h);
    return vec2(-b - h, -b + h);
}

mat2 rotate2d(float angle) {
    float c = cos(angle);
    float s = sin(angle);
    return mat2(c, -s, s, c);
}

float bell(float value, float width) {
    float x = value / max(width, 0.0001);
    return exp(-x * x);
}

vec3 colorRamp(float value) {
    vec3 hot = vec3(1.00, 0.80, 0.56);
    vec3 warm = vec3(0.52, 0.17, 0.055);
    vec3 ember = vec3(0.07, 0.012, 0.006);
    float first = smoothstep(0.03, 0.43, value);
    float second = smoothstep(0.43, 0.95, value);
    return mix(mix(hot, warm, first), ember, second);
}

void main() {
    vec2 screen = gl_FragCoord.xy / u_resolution;
    float aspect = u_resolution.x / u_resolution.y;
    vec2 p = (screen - 0.5) * vec2(aspect, 1.0);

    // Camera looks through a unit volume containing the accretion gas.
    vec3 rayOrigin = vec3(0.0, 0.0, 2.72);
    vec3 rayDirection = normalize(vec3(p * 1.88, -2.72));
    vec2 hit = raySphere(rayOrigin, rayDirection, 1.02);
    if (hit.x < 0.0) {
        fragColor = vec4(0.0);
        return;
    }

    float travel = max(hit.x, 0.0);
    vec3 rayPosition = rayOrigin + rayDirection * travel;
    float stepSize = (hit.y - travel) / float(MARCH_STEPS);
    vec3 accumulatedColor = vec3(0.0);
    float accumulatedAlpha = 0.0;
    float energy = clamp(u_energy, 0.0, 1.0);

    for (int i = 0; i < MARCH_STEPS; i++) {
        float radius = length(rayPosition);
        if (radius < 0.225) break;

        // A restrained inverse-square turn produces the folded upper and lower
        // images without turning the small hall object into a physics diagram.
        float bendFade = smoothstep(1.03, 0.40, radius);
        float bend = stepSize * 0.37 * bendFade / max(radius * radius, 0.055);
        rayDirection = normalize(rayDirection - normalize(rayPosition) * bend);
        rayPosition += rayDirection * stepSize;

        // The visible disk lies in XZ. Rotating its coordinates advects the
        // texture around the event horizon instead of merely pulsing opacity.
        float diskRadius = length(rayPosition.xz);
        float orbit = diskRadius * 4.27
                    - u_time * (0.34 + energy * 0.28) / max(diskRadius, 0.30);
        vec2 orbitPoint = rotate2d(orbit) * rayPosition.xz;
        vec2 noiseUV = orbitPoint * 0.86
                     + vec2(rayPosition.y * 0.31, u_time * 0.009);
        vec3 deepNoise = texture(u_noise, noiseUV).rgb;
        vec3 detailNoise = texture(u_noise, noiseUV * 2.017 + vec2(0.31, 0.67)).gbr;

        float thickness = mix(0.060, 0.072, deepNoise.b);
        float vertical = bell(rayPosition.y, thickness);
        float innerEdge = smoothstep(0.31, 0.43, diskRadius);
        float outerEdge = 1.0 - smoothstep(0.82, 1.01, diskRadius);
        float radialMask = innerEdge * outerEdge;

        float filament = deepNoise.r * 0.58 + detailNoise.g * 0.42;
        filament = 0.10 + pow(filament, 2.35) * 1.55;
        float density = vertical * radialMask * filament;

        float rampValue = diskRadius
                        + (deepNoise.g - 0.56) * 0.48
                        + (deepNoise.g - detailNoise.r) * 0.82;
        vec3 gasColor = colorRamp(rampValue);
        float innerHeat = 1.0 - smoothstep(0.36, 0.88, diskRadius);
        gasColor *= 0.74 + innerHeat * 1.72 + filament * 0.34;

        float localAlpha = 1.0 - exp(-density * stepSize * 13.5);
        float remaining = 1.0 - accumulatedAlpha;
        accumulatedColor += remaining * gasColor * localAlpha * 2.25;
        accumulatedAlpha += remaining * localAlpha;

        if (accumulatedAlpha > 0.985) break;
    }

    // Keep black pixels transparent: the hall draws its own event-horizon
    // shadow and can therefore composite this volume without a rectangle.
    vec3 mapped = vec3(1.0) - exp(-accumulatedColor * (1.18 + energy * 0.10));
    float luma = max(mapped.r, max(mapped.g, mapped.b));
    float alpha = smoothstep(0.006, 0.14, luma) * clamp(accumulatedAlpha * 1.6, 0.0, 1.0);
    fragColor = vec4(mapped, alpha);
}
