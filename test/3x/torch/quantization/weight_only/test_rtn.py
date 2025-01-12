import copy

import pytest
import torch
import transformers

from neural_compressor.torch.algorithms.weight_only.modules import WeightOnlyLinear
from neural_compressor.torch.quantization import (
    RTNConfig,
    convert,
    get_default_double_quant_config,
    get_default_rtn_config,
    prepare,
    quantize,
)


class TestRTNQuant:
    def setup_class(self):
        self.tiny_gptj = transformers.AutoModelForCausalLM.from_pretrained(
            "hf-internal-testing/tiny-random-GPTJForCausalLM",
        )
        self.example_inputs = torch.tensor([[10, 20, 30, 40, 50, 60]], dtype=torch.long)
        # record label for comparison
        self.label = self.tiny_gptj(self.example_inputs)[0]
        # test_default_config
        model = copy.deepcopy(self.tiny_gptj)
        quant_config = get_default_rtn_config()
        model = prepare(model, quant_config)
        model = convert(model)
        # record q_label for comparison
        self.q_label = model(self.example_inputs)[0]

    def teardown_class(self):
        pass

    @pytest.mark.parametrize(
        "bits, use_sym, group_size, group_dim",
        [
            (8, True, 128, 1),
            (4, True, 128, 1),
            (4, False, 32, 1),
            (4, True, 32, 0),
            (4, False, -1, 1),
            (2, True, 8, 1),
        ],
    )
    def test_int_params(self, bits, use_sym, group_size, group_dim):
        model = copy.deepcopy(self.tiny_gptj)
        quant_config = RTNConfig(
            bits=bits,
            use_sym=use_sym,
            group_size=group_size,
            group_dim=group_dim,
        )
        model = prepare(model, quant_config)
        model = convert(model)
        out = model(self.example_inputs)[0]
        assert (out != self.label).all(), "WOQ output should be different with raw output"
        if (bits, use_sym, group_size, group_dim) == (8, True, 128, 1):
            assert torch.allclose(out, self.label, atol=0.01), "Accuracy gap atol > 0.01 is unexpected."
        if (bits, use_sym, group_size, group_dim) == [(4, True, 128, 0), (4, True, 32, 1)]:
            assert torch.allclose(out, self.label, atol=0.1), "Accuracy gap atol > 0.1 is unexpected."
        if (bits, use_sym, group_size, group_dim) == [(4, False, 32, 0), (4, False, -1, 1), (2, True, 8, 1)]:
            assert torch.allclose(out, self.label, atol=0.5), "Accuracy gap atol > 0.5 is unexpected."

    def test_full_range(self):
        # use_full_range=False, full_range specific to sym
        model = copy.deepcopy(self.tiny_gptj)
        quant_config = RTNConfig(
            use_sym=True,
            use_full_range=False,
        )
        model = prepare(model, quant_config)
        model = convert(model)
        out = model(self.example_inputs)[0]
        atol_false = (out - self.label).amax()
        # use_full_range=True
        model = copy.deepcopy(self.tiny_gptj)
        quant_config = RTNConfig(
            use_sym=True,
            use_full_range=True,
        )
        model = prepare(model, quant_config)
        model = convert(model)
        out = model(self.example_inputs)[0]
        atol_true = (out - self.label).amax()
        # compare atol, this case is an ideal case.
        assert (
            atol_false > atol_true
        ), "use_full_range=True doesn't help accuracy, maybe is reasonable, please double check."

    def test_mse_search(self):
        # use_mse_search=False
        model = copy.deepcopy(self.tiny_gptj)
        quant_config = RTNConfig(
            use_mse_search=False,
        )
        model = prepare(model, quant_config)
        model = convert(model)
        out = model(self.example_inputs)[0]
        atol_false = (out - self.label).amax()
        # use_mse_search=True
        model = copy.deepcopy(self.tiny_gptj)
        quant_config = RTNConfig(
            use_mse_search=True,
        )
        model = prepare(model, quant_config)
        model = convert(model)
        out = model(self.example_inputs)[0]
        atol_true = (out - self.label).amax()
        # compare atol, this case is not an ideal case.
        try:
            assert (
                atol_false > atol_true
            ), "use_mse_search=True doesn't help accuracy, maybe is reasonable, please double check."
        except:
            assert torch.allclose(atol_false, atol_true, atol=0.012), "atol is very close, double checked the logic."

    def test_layer_wise(self):
        model = copy.deepcopy(self.tiny_gptj)
        quant_config = RTNConfig(
            use_layer_wise=True,
        )
        model = prepare(model, quant_config)
        model = convert(model)
        # TODO: (Xin) not implemented

    @pytest.mark.parametrize("dtype", ["int4", "nf4", "fp4"])
    def test_export_compressed_model(self, dtype):
        if dtype == "int4":
            # using optimum format as default
            model = copy.deepcopy(self.tiny_gptj)
            quant_config = RTNConfig(
                dtype=dtype,
                export_compressed_model=True,
            )
            model = prepare(model, quant_config)
            model = convert(model)
            out = model(self.example_inputs)[0]
            assert isinstance(model.lm_head, WeightOnlyLinear), "Exporting compressed model failed."
            atol_true = (out - self.q_label).amax()
            # The small gap is caused by FP16 scale in WeightOnlyLinear.
            assert (
                atol_true < 0.0005
            ), "Exporting compressed model should have the same output as quantized model. Please double check"
        else:
            # optimum_format doesn't suit for symmetric nf4 fp4.
            model = copy.deepcopy(self.tiny_gptj)
            quant_config = RTNConfig(
                dtype=dtype,
                export_compressed_model=False,
            )
            model = prepare(model, quant_config)
            model = convert(model)
            out1 = model(self.example_inputs)[0]
            model = copy.deepcopy(self.tiny_gptj)
            quant_config = RTNConfig(
                dtype=dtype,
                export_compressed_model=True,
            )
            model = prepare(model, quant_config)
            model = convert(model)
            out2 = model(self.example_inputs)[0]
            assert isinstance(model.lm_head, WeightOnlyLinear), "Exporting compressed model failed."
            assert torch.allclose(
                out1, out2
            ), "Exporting compressed model should have the same output as quantized model. Please double check"

    @pytest.mark.parametrize(
        "dtype",
        ["int4", "nf4", "fp4", "fp4_e2m1_bnb", "fp4_e2m1", "fp8_e5m2", "fp8_e5m2fnuz", "fp8_e4m3fn", "fp8_e4m3fnuz"],
    )
    def test_dtype_params(self, dtype):
        model = copy.deepcopy(self.tiny_gptj)
        quant_config = RTNConfig(
            dtype=dtype,
        )
        model = prepare(model, quant_config)
        model = convert(model)
        out = model(self.example_inputs)[0]
        assert torch.allclose(out, self.label, atol=0.11), "Accuracy gap atol > 0.11 is unexpected."

    @pytest.mark.parametrize("dtype", ["int4", "nf4"])
    @pytest.mark.parametrize("double_quant_bits", [6])
    @pytest.mark.parametrize("double_quant_group_size", [8, 256])
    # TODO: (Xin) to implement
    # @pytest.mark.parametrize('export_compressed_model', [False, True])
    def test_double_quant_params(self, dtype, double_quant_bits, double_quant_group_size):
        model = copy.deepcopy(self.tiny_gptj)
        # double_quant_use_sym = False
        quant_config = RTNConfig(
            dtype=dtype,
            use_double_quant=True,
            double_quant_bits=double_quant_bits,
            double_quant_use_sym=False,
            double_quant_group_size=double_quant_group_size,
        )
        model = prepare(model, quant_config)
        model = convert(model)
        out = model(self.example_inputs)[0]
        atol_false = (out - self.q_label).amax()
        model = copy.deepcopy(self.tiny_gptj)
        # double_quant_use_sym = True
        quant_config = RTNConfig(
            dtype=dtype,
            use_double_quant=True,
            double_quant_bits=double_quant_bits,
            double_quant_use_sym=True,
            double_quant_group_size=double_quant_group_size,
        )
        model = prepare(model, quant_config)
        model = convert(model)
        out = model(self.example_inputs)[0]
        atol_true = (out - self.q_label).amax()
        # compare atol, this case is an ideal case.
        assert (
            atol_false < atol_true
        ), "asym for double quant should have smaller atol because scales is bigger than zero, please double check."

    def test_double_quant_constants(self):
        model = copy.deepcopy(self.tiny_gptj)
        # the same as get_default_double_quant_config(type="BNB_NF4")
        double_quant_config_dict = get_default_double_quant_config()
        model = prepare(model, double_quant_config_dict)
        model = convert(model)
        out = model(self.example_inputs)[0]
        assert torch.allclose(out, self.label, atol=0.1), "Accuracy gap atol > 0.1 is unexpected."
        # type="BNB_NF4"
        model = copy.deepcopy(self.tiny_gptj)
        double_quant_config_dict = get_default_double_quant_config(type="BNB_NF4")
        model = prepare(model, double_quant_config_dict)
        model = convert(model)
        out1 = model(self.example_inputs)[0]
        assert torch.allclose(out, out1), "Accuracy should be the same, please double check."
        # type="GGML_TYPE_Q4_K"
        model = copy.deepcopy(self.tiny_gptj)
        double_quant_config_dict = get_default_double_quant_config(type="GGML_TYPE_Q4_K")
        model = prepare(model, double_quant_config_dict)
        model = convert(model)
        out2 = model(self.example_inputs)[0]
        assert torch.allclose(out2, self.label, atol=0.1), "Accuracy gap atol > 0.1 is unexpected."

    def test_rtn_with_quantize_API(self):
        quant_config = get_default_rtn_config()

        # prepare + convert API
        model = copy.deepcopy(self.tiny_gptj)
        model = quantize(model, quant_config)
        output_1 = model(self.example_inputs)[0]

        # quantize API
        model = copy.deepcopy(self.tiny_gptj)
        model = prepare(model, quant_config)
        model = convert(model)
        output_2 = model(self.example_inputs)[0]

        # compare the results of calling `convert` + `prepare` and calling `quantize`
        assert torch.all(
            output_1.eq(output_2)
        ), "The results of calling `convert` + `prepare` and calling `quantize` should be equal."
