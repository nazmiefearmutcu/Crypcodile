use pyo3::prelude::*;
use pyo3::types::{PyDict, PyBytes};

#[pyfunction]
fn decode_uniswap_v3_swap(py: Python<'_>, topics: Vec<String>, data: String) -> PyResult<PyObject> {
    if topics.len() < 3 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "Uniswap V3 Swap log must have at least 3 topics"
        ));
    }
    
    let eth_utils = py.import("eth_utils")?;
    let to_checksum_address = eth_utils.getattr("to_checksum_address")?;
    
    let clean_addr = |t: &str| -> PyResult<PyObject> {
        let s = if t.starts_with("0x") || t.starts_with("0X") {
            &t[2..]
        } else {
            t
        };
        if s.len() != 64 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                format!("Topic must be 32 bytes (64 hex chars), got {}", s.len())
            ));
        }
        let addr_hex = format!("0x{}", &s[24..]);
        to_checksum_address.call1((addr_hex,))
    };
    
    let sender = clean_addr(&topics[1])?;
    let recipient = clean_addr(&topics[2])?;
    
    let data_hex = if data.starts_with("0x") || data.starts_with("0X") {
        &data[2..]
    } else {
        &data
    };
    
    let data_bytes = (0..data_hex.len())
        .step_by(2)
        .map(|i| {
            u8::from_str_radix(&data_hex[i..i+2], 16)
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("Invalid hex: {}", e)))
        })
        .collect::<PyResult<Vec<u8>>>()?;
        
    if data_bytes.len() != 160 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            format!("Uniswap V3 Swap log data must be exactly 160 bytes, got {}", data_bytes.len())
        ));
    }
    
    let int_from_bytes = py.eval("int.from_bytes", None, None)?;
    
    let decode_int256 = |bytes: &[u8]| -> PyResult<PyObject> {
        let py_bytes = PyBytes::new(py, bytes);
        int_from_bytes.call1((py_bytes, "big", true))
    };
    
    let decode_uint256 = |bytes: &[u8]| -> PyResult<PyObject> {
        let py_bytes = PyBytes::new(py, bytes);
        int_from_bytes.call1((py_bytes, "big", false))
    };
    
    let amount0 = decode_int256(&data_bytes[0..32])?;
    let amount1 = decode_int256(&data_bytes[32..64])?;
    let sqrt_price_x96 = decode_uint256(&data_bytes[64..96])?;
    let liquidity = decode_uint256(&data_bytes[96..128])?;
    let tick = decode_int256(&data_bytes[128..160])?;
    
    let dict = PyDict::new(py);
    dict.set_item("sender", sender)?;
    dict.set_item("recipient", recipient)?;
    dict.set_item("amount0", amount0)?;
    dict.set_item("amount1", amount1)?;
    dict.set_item("sqrtPriceX96", sqrt_price_x96)?;
    dict.set_item("liquidity", liquidity)?;
    dict.set_item("tick", tick)?;
    
    Ok(dict.to_object(py))
}

#[pyfunction]
fn decode_aerodrome_v2_swap(py: Python<'_>, topics: Vec<String>, data: String) -> PyResult<PyObject> {
    if topics.len() < 3 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "Aerodrome V2 Swap log must have at least 3 topics"
        ));
    }
    
    let eth_utils = py.import("eth_utils")?;
    let to_checksum_address = eth_utils.getattr("to_checksum_address")?;
    
    let clean_addr = |t: &str| -> PyResult<PyObject> {
        let s = if t.starts_with("0x") || t.starts_with("0X") {
            &t[2..]
        } else {
            t
        };
        if s.len() != 64 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                format!("Topic must be 32 bytes (64 hex chars), got {}", s.len())
            ));
        }
        let addr_hex = format!("0x{}", &s[24..]);
        to_checksum_address.call1((addr_hex,))
    };
    
    let sender = clean_addr(&topics[1])?;
    let recipient = clean_addr(&topics[2])?;
    
    let data_hex = if data.starts_with("0x") || data.starts_with("0X") {
        &data[2..]
    } else {
        &data
    };
    
    let data_bytes = (0..data_hex.len())
        .step_by(2)
        .map(|i| {
            u8::from_str_radix(&data_hex[i..i+2], 16)
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("Invalid hex: {}", e)))
        })
        .collect::<PyResult<Vec<u8>>>()?;
        
    if data_bytes.len() != 128 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            format!("Aerodrome V2 Swap log data must be exactly 128 bytes, got {}", data_bytes.len())
        ));
    }
    
    let int_from_bytes = py.eval("int.from_bytes", None, None)?;
    
    let decode_uint256 = |bytes: &[u8]| -> PyResult<PyObject> {
        let py_bytes = PyBytes::new(py, bytes);
        int_from_bytes.call1((py_bytes, "big", false))
    };
    
    let amount0_in = decode_uint256(&data_bytes[0..32])?;
    let amount1_in = decode_uint256(&data_bytes[32..64])?;
    let amount0_out = decode_uint256(&data_bytes[64..96])?;
    let amount1_out = decode_uint256(&data_bytes[96..128])?;
    
    let dict = PyDict::new(py);
    dict.set_item("sender", sender)?;
    dict.set_item("recipient", recipient)?;
    dict.set_item("amount0In", amount0_in)?;
    dict.set_item("amount1In", amount1_in)?;
    dict.set_item("amount0Out", amount0_out)?;
    dict.set_item("amount1Out", amount1_out)?;
    
    Ok(dict.to_object(py))
}

#[pymodule]
fn _rust_decoder(_py: Python<'_>, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(decode_uniswap_v3_swap, m)?)?;
    m.add_function(wrap_pyfunction!(decode_aerodrome_v2_swap, m)?)?;
    Ok(())
}
