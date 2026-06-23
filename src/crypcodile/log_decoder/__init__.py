try:
    from ._rust_decoder import decode_uniswap_v3_swap, decode_aerodrome_v2_swap
    HAS_RUST_DECODER = True
except ImportError:
    from .fallback import decode_uniswap_v3_swap, decode_aerodrome_v2_swap
    HAS_RUST_DECODER = False

__all__ = ["decode_uniswap_v3_swap", "decode_aerodrome_v2_swap", "HAS_RUST_DECODER"]
