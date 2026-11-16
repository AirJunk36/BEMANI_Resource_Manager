#version 450
layout(binding = 0) uniform sampler2D sourceTexture;
layout(binding = 1) uniform sampler2D destinationTexture;
layout(binding = 2) uniform sampler2D maskTexture;
layout(location = 0) in vec2 uv;
layout(location = 1) in vec4 mul;
layout(location = 2) in vec4 add;
layout(location = 3) in vec2 sceneUV;
layout(location = 4) flat in float mode;
layout(location = 0) out vec4 fragment;
void main() {
    if (any(lessThan(uv, vec2(0.0))) || any(greaterThan(uv, vec2(1.0)))) discard;
    vec4 c = clamp(texture(sourceTexture, uv) * mul + add, 0.0, 1.0);
    if (mode >= 16.0) {
        float maskAlpha = texture(maskTexture, sceneUV).a;
        c.a *= mode >= 32.0 ? 1.0 - maskAlpha : maskAlpha;
    }
    vec4 s = vec4(c.rgb * c.a, c.a);
    float blend = mod(mode, 16.0);
    if (blend == 8.0 || blend == 9.0) s.a = max(s.r, max(s.g, s.b));
    if (mod(mode, 16.0) == 3.0) {
        vec4 d = texture(destinationTexture, sceneUV);
        fragment = vec4(s.rgb * d.rgb + s.rgb * (1.0 - d.a) + d.rgb * (1.0 - s.a), s.a + d.a - s.a * d.a);
    } else {
        fragment = s;
    }
}
