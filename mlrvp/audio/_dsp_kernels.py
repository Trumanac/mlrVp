"""Numba JIT-compiled DSP kernels for per-sample audio processing.

These replace the pure-Python inner loops in effects.py with native
machine code compiled via LLVM.  Typical speedup: 30-100x.

Each kernel takes plain floats / numpy arrays and returns updated state.
No Python objects allowed inside @njit functions.
"""

import math
from numba import njit

# ── Moog Ladder Filter (4-pole, 24dB/oct) ────────────────────────────

@njit(cache=True, nogil=True)
def moog_ladder_kernel(
    x_list,        # float64[n] — input samples for one channel
    n,             # int
    g,             # float64 — one-pole coefficient
    resonance,     # float64
    drive,         # float64
    do_drive,      # bool
    y0, y1, y2, y3, fb,  # float64 — filter state
    result,        # float64[n] — output buffer (written in-place)
):
    """Process one channel of the Moog ladder filter."""
    # Seed cached tanh values
    ty0 = math.tanh(y0)
    ty1 = math.tanh(y1)
    ty2 = math.tanh(y2)
    ty3 = math.tanh(y3)

    for i in range(n):
        xn = x_list[i]
        if do_drive:
            xn = math.tanh(xn * drive)
        xn -= resonance * fb

        txn = math.tanh(xn)
        ny0 = y0 + g * (txn - ty0);  ty0 = math.tanh(ny0)
        ny1 = y1 + g * (ty0 - ty1);  ty1 = math.tanh(ny1)
        ny2 = y2 + g * (ty1 - ty2);  ty2 = math.tanh(ny2)
        ny3 = y3 + g * (ty2 - ty3);  ty3 = math.tanh(ny3)
        y0 = ny0; y1 = ny1; y2 = ny2; y3 = ny3
        fb = ny3
        result[i] = ny3

    return y0, y1, y2, y3, fb


# ── Phaser (cascaded allpass with feedback) ──────────────────────────

@njit(cache=True, nogil=True)
def phaser_kernel(
    data,          # float32[n, 2] — stereo input
    n,             # int
    out,           # float32[n, 2] — stereo output (written in-place)
    rps,           # float64 — LFO phase increment per sample
    phase,         # float64 — current LFO phase
    feedback,      # float64
    stages,        # int
    ap_x,          # float64[stages, 2] — allpass x state
    ap_y,          # float64[stages, 2] — allpass y state
    fb0,           # float64 — feedback sample L
    fb1,           # float64 — feedback sample R
):
    """Process stereo phaser with per-sample allpass cascade."""
    _pi2 = 2.0 * math.pi

    for i in range(n):
        # LFO -> allpass coefficient
        lfo = math.sin(_pi2 * phase) * 0.5  # depth is baked into the call
        c = 0.5 + lfo * 0.4
        if c < 0.05:
            c = 0.05
        elif c > 0.95:
            c = 0.95
        mc = -c
        phase += rps
        if phase >= 1.0:
            phase -= 1.0

        # Left channel
        x0 = float(data[i, 0]) + fb0 * feedback
        for s in range(stages):
            xp = ap_x[s, 0]
            yp = ap_y[s, 0]
            y = mc * x0 + xp + c * yp
            ap_x[s, 0] = x0
            ap_y[s, 0] = y
            x0 = y
        fb0 = x0

        # Right channel
        x1 = float(data[i, 1]) + fb1 * feedback
        for s in range(stages):
            xp = ap_x[s, 1]
            yp = ap_y[s, 1]
            y = mc * x1 + xp + c * yp
            ap_x[s, 1] = x1
            ap_y[s, 1] = y
            x1 = y
        fb1 = x1

        out[i, 0] = float(data[i, 0]) * 0.5 + x0 * 0.5
        out[i, 1] = float(data[i, 1]) * 0.5 + x1 * 0.5

    return phase, fb0, fb1


# ── Compressor (envelope follower + gain reduction) ──────────────────

@njit(cache=True, nogil=True)
def compressor_kernel(
    sc_mono,       # float32[n] — sidechain mono level
    n,             # int
    gain,          # float32[n] — output gain buffer (written in-place)
    thresh_lin,    # float64
    ratio,         # float64
    alpha_a,       # float64 — attack coefficient
    alpha_r,       # float64 — release coefficient
    env,           # float64 — envelope state
):
    """Compute compressor gain reduction per sample."""
    gr_sum = 0.0
    inv_r = 1.0 - 1.0 / ratio

    for i in range(n):
        level = float(sc_mono[i])
        # Envelope follower
        if level > env:
            env = alpha_a * env + (1.0 - alpha_a) * level
        else:
            env = alpha_r * env + (1.0 - alpha_r) * level

        # Gain computer
        if env > thresh_lin:
            g = (thresh_lin / env) ** inv_r
            gr_sum += math.log10(max(g, 1e-10)) * 20.0
            gain[i] = g
        else:
            gain[i] = 1.0

    gr_db = gr_sum / max(n, 1)
    return env, gr_db


# ── Glue Compressor (SSL-style with soft knee + auto-release) ────────

@njit(cache=True, nogil=True)
def glue_compressor_kernel(
    sc_mono,           # float32[n]
    n,                 # int
    gain,              # float32[n] — output (written in-place)
    thresh_lin,        # float64
    inv_ratio,         # float64
    alpha_a,           # float64
    alpha_r,           # float64
    knee_lower_lin,    # float64
    knee_upper_lin,    # float64
    knee_range_lin,    # float64
    auto_release,      # bool
    sr_ms,             # float64 — 0.001 * sample_rate
    env,               # float64
):
    """Compute glue compressor gain with soft knee and optional auto-release."""
    gr_sum = 0.0

    for i in range(n):
        level = float(sc_mono[i])

        if level > env:
            env = alpha_a * env + (1.0 - alpha_a) * level
        else:
            if auto_release:
                gr_ratio = env / thresh_lin
                auto_rel_ms = max(50.0, 600.0 / max(gr_ratio, 1.0))
                alpha_r_auto = math.exp(-1.0 / (auto_rel_ms * sr_ms))
                env = alpha_r_auto * env + (1.0 - alpha_r_auto) * level
            else:
                env = alpha_r * env + (1.0 - alpha_r) * level

        # Soft-knee gain computer
        if env <= knee_lower_lin:
            g = 1.0
        elif env >= knee_upper_lin:
            g = (thresh_lin / env) ** (1.0 - inv_ratio)
        else:
            t = (env - knee_lower_lin) / knee_range_lin
            full_g = (thresh_lin / env) ** (1.0 - inv_ratio)
            g = 1.0 + (full_g - 1.0) * t * t

        gain[i] = g
        if g < 1.0:
            gr_sum += math.log10(g) * 20.0

    gr_db = gr_sum / max(n, 1)
    return env, gr_db


# ── Channel playback (position + interpolation + envelope) ───────────

@njit(cache=True, nogil=True)
def channel_process_kernel(
    sample_data,       # float32[N, C] — full sample data
    num_sample_frames, # int — total frames in sample
    num_channels,      # int — 1=mono, 2=stereo
    output,            # float32[n, 2] — stereo output (written in-place)
    num_frames,        # int — frames to process
    position,          # float64 — starting playback position
    speed,             # float64 — playback rate (negative = reverse)
    loop_start,        # float64
    loop_end,          # float64
    do_loop,           # bool — True = wrap, False = stop at boundary
    env,               # float64 — current envelope level
    is_releasing,      # bool
    attack_samples,    # float64 — attack time in samples
    release_samples,   # float64 — release time in samples
    gain_from,         # float64 — smoothed gain at block start
    gain_to,           # float64 — target gain (gain * velocity, 0 when muted)
    gain_step,         # float64 — max per-sample gain change (click-free ramp)
    # ── Crossfade tail (mixed into output in same pass) ──
    xfade_buf,         # float32[XLEN, 2] — pre-rendered xfade tail
    xfade_pos,         # int — current read position in xfade_buf
    xfade_len,         # int — length of active xfade (0 = inactive)
    # ── Fade-in ramp after retrigger ──
    fade_in_ramp,      # float32[XLEN] — sin-based 0→1 ramp
    fade_in_offset,    # int — how far into the ramp we are (XLEN - remaining)
    fade_in_remaining, # int — samples left of fade-in (0 = inactive)
):
    """Process one channel: position, interpolation, envelope, smoothed
    gain (incl. mute), crossfade-tail mix and fade-in ramp in one native pass.

    Returns (new_position, new_envelope, active_frames, stopped,
             new_xfade_pos, new_fade_in_remaining, new_gain).
    """
    is_stereo = num_channels >= 2
    max_pos = float(num_sample_frames - 1)
    loop_len = loop_end - loop_start
    if loop_len <= 0.0:
        # Still need to mix xfade tail even when nothing plays
        xf_p = xfade_pos
        if xfade_len > 0 and xf_p < xfade_len:
            n = min(xfade_len - xf_p, num_frames)
            for i in range(n):
                output[i, 0] += xfade_buf[xf_p + i, 0]
                output[i, 1] += xfade_buf[xf_p + i, 1]
            xf_p += n
        return position, env, 0, True, xf_p, 0, gain_to

    att_rate = 1.0 / attack_samples if attack_samples > 0.0 else 1.0
    rel_rate = 1.0 / release_samples if release_samples > 0.0 else 1.0
    active = num_frames
    stopped = False

    fi_off = fade_in_offset
    fi_rem = fade_in_remaining
    xf_p = xfade_pos
    g = gain_from

    for i in range(num_frames):
        # ── Envelope ──
        if is_releasing:
            env -= rel_rate
            if env <= 0.0:
                env = 0.0
                active = i
                stopped = True
                break
        else:
            if env < 1.0:
                env += att_rate
                if env > 1.0:
                    env = 1.0

        # ── Position ──
        pos = position + i * speed

        # ── Boundary / loop ──
        if do_loop:
            if speed >= 0.0:
                pos = loop_start + ((pos - loop_start) % loop_len)
            else:
                pos = loop_end - ((loop_end - pos) % loop_len)
        else:
            if speed > 0.0 and pos >= loop_end:
                active = i
                stopped = True
                break
            elif speed < 0.0 and pos < loop_start:
                active = i
                stopped = True
                break

        # ── Clamp ──
        if pos < 0.0:
            pos = 0.0
        elif pos > max_pos:
            pos = max_pos

        # ── Linear interpolation ──
        idx = int(pos)
        frac = pos - idx
        idx1 = idx + 1
        if idx1 > num_sample_frames - 1:
            idx1 = num_sample_frames - 1

        inv_frac = 1.0 - frac
        s_l = sample_data[idx, 0] * inv_frac + sample_data[idx1, 0] * frac
        # ── Gain ramp toward target (mute / solo / fader changes) ──
        if g < gain_to:
            g = min(g + gain_step, gain_to)
        elif g > gain_to:
            g = max(g - gain_step, gain_to)
        eg = env * g

        o_l = s_l * eg
        if is_stereo:
            o_r = (sample_data[idx, 1] * inv_frac + sample_data[idx1, 1] * frac) * eg
        else:
            o_r = o_l

        # ── Fade-in ramp ──
        if fi_rem > 0:
            r = fade_in_ramp[fi_off]
            o_l *= r
            o_r *= r
            fi_off += 1
            fi_rem -= 1

        # ── Crossfade tail ──
        if xfade_len > 0 and xf_p < xfade_len:
            o_l += xfade_buf[xf_p, 0]
            o_r += xfade_buf[xf_p, 1]
            xf_p += 1

        output[i, 0] = o_l
        output[i, 1] = o_r

    # Mix remaining xfade tail past active region
    if xfade_len > 0 and xf_p < xfade_len:
        remaining_xf = min(xfade_len - xf_p, num_frames - active)
        for j in range(remaining_xf):
            output[active + j, 0] += xfade_buf[xf_p + j, 0]
            output[active + j, 1] += xfade_buf[xf_p + j, 1]
        xf_p += remaining_xf

    # Final position
    new_pos = position + active * speed
    if do_loop and loop_len > 0.0:
        if speed >= 0.0:
            new_pos = loop_start + ((new_pos - loop_start) % loop_len)
        else:
            new_pos = loop_end - ((loop_end - new_pos) % loop_len)

    return new_pos, env, active, stopped, xf_p, fi_rem, g


# ── Crossfade tail snapshot (for retrigger / pitch swap) ─────────────

@njit(cache=True, nogil=True)
def xfade_tail_kernel(
    sample_data,       # float32[N, C]
    num_sample_frames, # int
    num_channels,      # int
    position,          # float64
    speed,             # float64
    xfade_buf,         # float32[xlen, 2] — output written in-place
    xfade_fade,        # float32[xlen] — cosine fade-out curve
    xlen,              # int
    env_gain,          # float64 — envelope * gain * velocity
):
    """Render a short fade-out tail from current position.

    Replaces ~13 numpy operations with one native pass.
    """
    if num_sample_frames < 1:
        for i in range(xlen):
            xfade_buf[i, 0] = 0.0
            xfade_buf[i, 1] = 0.0
        return

    is_stereo = num_channels >= 2
    max_pos = float(num_sample_frames - 1)

    for i in range(xlen):
        pos = position + i * speed
        if pos < 0.0:
            pos = 0.0
        elif pos > max_pos:
            pos = max_pos

        idx = int(pos)
        frac = pos - idx
        idx1 = idx + 1
        if idx1 > num_sample_frames - 1:
            idx1 = num_sample_frames - 1

        inv_frac = 1.0 - frac
        s_l = sample_data[idx, 0] * inv_frac + sample_data[idx1, 0] * frac
        f = xfade_fade[i] * env_gain

        xfade_buf[i, 0] = s_l * f
        if is_stereo:
            s_r = sample_data[idx, 1] * inv_frac + sample_data[idx1, 1] * frac
            xfade_buf[i, 1] = s_r * f
        else:
            xfade_buf[i, 1] = s_l * f


# ── Gated Reverb — gate envelope kernel ──────────────────────────────

@njit(cache=True, nogil=True)
def gate_envelope_kernel(
    levels,      # float32[n] — per-sample input levels
    n,           # int
    gate_dur,    # int — gate duration in samples
    fade_dec,    # float64 — 1 / fade_samples
    thresh,      # float64 — trigger threshold
    gate_open,   # bool — initial state
    countdown,   # int — initial countdown
    gate_env,    # float64 — initial envelope value
    env_out,     # float32[n] — output envelope (written in-place)
):
    """Build gate envelope: open on trigger, hold for gate_dur, fade out.

    Returns (gate_open, countdown, gate_env) final state.
    """
    for i in range(n):
        if levels[i] > thresh and not gate_open:
            gate_open = True
            countdown = gate_dur
            gate_env = 1.0

        if gate_open:
            countdown -= 1
            if countdown <= 0:
                gate_env -= fade_dec
                if gate_env <= 0.0:
                    gate_env = 0.0
                    gate_open = False

        env_out[i] = gate_env

    return gate_open, countdown, gate_env


# ── Transient Shaper — envelope follower kernel ──────────────────────

@njit(cache=True, nogil=True)
def transient_env_kernel(
    rect,          # float64[n, n_ch] — rectified input
    n,             # int
    n_ch,          # int
    fast_att,      # float64
    fast_rel,      # float64
    slow_att,      # float64
    slow_rel,      # float64
    fast_env_state,  # float64[n_ch] — per-channel fast env state (read/write)
    slow_env_state,  # float64[n_ch] — per-channel slow env state (read/write)
    fast_env_out,    # float64[n, n_ch] — output (written in-place)
    slow_env_out,    # float64[n, n_ch] — output (written in-place)
):
    """Dual envelope follower (fast + slow) for transient detection."""
    for ch in range(n_ch):
        fe = fast_env_state[ch]
        se = slow_env_state[ch]
        for i in range(n):
            s = rect[i, ch]
            if s > fe:
                fe = fast_att * fe + (1.0 - fast_att) * s
            else:
                fe = fast_rel * fe + (1.0 - fast_rel) * s
            if s > se:
                se = slow_att * se + (1.0 - slow_att) * s
            else:
                se = slow_rel * se + (1.0 - slow_rel) * s
            fast_env_out[i, ch] = fe
            slow_env_out[i, ch] = se
        fast_env_state[ch] = fe
        slow_env_state[ch] = se


# ── Biquad filter (Direct Form II Transposed, stereo in-place) ──────

@njit(cache=True, nogil=True)
def biquad_filter_kernel(
    data,    # float32[n, 2]  — input/output (overwritten in-place)
    n,       # int            — number of frames
    b0,      # float64        — feedforward coefficients
    b1,      # float64
    b2,      # float64
    a1,      # float64        — feedback coefficients (a0 = 1)
    a2,      # float64
    z,       # float64[2, 2]  — filter state [delay_line, channel]
):
    """Apply a biquad filter in-place to stereo float32 audio.

    Uses Direct Form II Transposed for numerical stability.
    State ``z`` is updated in-place so it persists across calls.
    """
    for ch in range(2):
        z0 = z[0, ch]
        z1 = z[1, ch]
        for i in range(n):
            x = float(data[i, ch])
            y = b0 * x + z0
            z0 = b1 * x - a1 * y + z1
            z1 = b2 * x - a2 * y
            data[i, ch] = float(y)
        z[0, ch] = z0
        z[1, ch] = z1
        # Flush denormals
        if abs(z[0, ch]) < 1e-18:
            z[0, ch] = 0.0
        if abs(z[1, ch]) < 1e-18:
            z[1, ch] = 0.0
