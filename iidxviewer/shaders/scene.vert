#version 450
layout(location = 0) in vec2 position;
layout(location = 1) in vec2 texcoord;
layout(location = 2) in vec4 multiplyColor;
layout(location = 3) in vec4 addColor;
layout(location = 4) in float blendMode;
layout(location = 0) out vec2 uv;
layout(location = 1) out vec4 mul;
layout(location = 2) out vec4 add;
layout(location = 3) out vec2 sceneUV;
layout(location = 4) flat out float mode;
void main() {
    gl_Position = vec4(position, 0.0, 1.0);
    uv = texcoord;
    mul = multiplyColor;
    add = addColor;
    sceneUV = vec2(position.x * 0.5 + 0.5, 0.5 - position.y * 0.5);
    mode = blendMode;
}
