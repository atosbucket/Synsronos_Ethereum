'use strict';

const { artifacts, contract } = require('hardhat');
const { assert, addSnapshotBeforeRestoreAfterEach } = require('../contracts/common');
const { multiplyDecimal, toUnit } = require('../utils')();

const {
	ensureOnlyExpectedMutativeFunctions,
	onlyGivenAddressCanInvoke,
	setupPriceAggregators,
	updateAggregatorRates,
} = require('../contracts/helpers');

const { setupAllContracts } = require('../contracts/setup');
const { toBytes32 } = require('../..');

contract('DynamicSynthRedeemer', async accounts => {
	const synths = ['sUSD', 'sBTC', 'sETH', 'ETH', 'SNX'];
	const [sBTC, sETH, ETH] = ['sBTC', 'sETH', 'ETH'].map(toBytes32);
	const [priceBTC, priceETH] = ['70000', '3500'].map(toUnit);

	const [, owner, , , account1] = accounts;

	let instance;
	let addressResolver,
		dynamicSynthRedeemer,
		etherWrapper,
		exchangeRates,
		issuer,
		proxysBTC,
		proxysETH,
		proxysUSD,
		proxySynthetix,
		synthetix,
		systemSettings,
		wrapperFactory,
		weth;

	before(async () => {
		({
			AddressResolver: addressResolver,
			DynamicSynthRedeemer: dynamicSynthRedeemer,
			ExchangeRates: exchangeRates,
			Issuer: issuer,
			ProxyERC20sBTC: proxysBTC,
			ProxyERC20sETH: proxysETH,
			ProxyERC20sUSD: proxysUSD,
			ProxyERC20Synthetix: proxySynthetix,
			Synthetix: synthetix,
			SystemSettings: systemSettings,
			WrapperFactory: wrapperFactory,
			WETH: weth,
		} = await setupAllContracts({
			accounts,
			synths,
			contracts: [
				'AddressResolver',
				'DebtCache',
				'DynamicSynthRedeemer',
				'Exchanger',
				'ExchangeRates',
				'Issuer',
				'Liquidator',
				'LiquidatorRewards',
				'ProxyERC20',
				'RewardEscrowV2',
				'Synthetix',
				'SystemSettings',
				'WrapperFactory',
				'WETH',
			],
		}));

		// use implementation ABI on the proxy address to simplify calling
		synthetix = await artifacts.require('Synthetix').at(proxySynthetix.address);

		// setup aggregators
		await setupPriceAggregators(exchangeRates, owner, [sBTC, sETH, ETH]);
		await updateAggregatorRates(
			exchangeRates,
			null,
			[sBTC, sETH, ETH],
			[priceBTC, priceETH, priceETH]
		);

		// deploy an eth wrapper
		const etherWrapperCreateTx = await wrapperFactory.createWrapper(
			weth.address,
			sETH,
			toBytes32('SynthsETH'),
			{ from: owner }
		);

		// extract address from events
		const etherWrapperAddress = etherWrapperCreateTx.logs.find(l => l.event === 'WrapperCreated')
			.args.wrapperAddress;
		etherWrapper = await artifacts.require('Wrapper').at(etherWrapperAddress);

		// setup eth wrapper
		await systemSettings.setWrapperMaxTokenAmount(etherWrapperAddress, toUnit('5000'), {
			from: owner,
		});
	});

	addSnapshotBeforeRestoreAfterEach();

	it('ensure only known functions are mutative', () => {
		ensureOnlyExpectedMutativeFunctions({
			abi: dynamicSynthRedeemer.abi,
			ignoreParents: ['Owned', 'MixinResolver'],
			expected: [
				'redeem',
				'redeemAll',
				'redeemPartial',
				'setDiscountRate',
				'resumeRedemption',
				'suspendRedemption',
			],
		});
	});

	describe('On contract deployment', async () => {
		beforeEach(async () => {
			instance = dynamicSynthRedeemer;
		});

		it('should set constructor params', async () => {
			assert.equal(await instance.owner(), owner);
			assert.equal(await instance.resolver(), addressResolver.address);
		});

		it('should set default discount rate', async () => {
			assert.bnEqual(await instance.getDiscountRate(), toUnit('1'));
		});

		it('should not be active for redemption', async () => {
			assert.equal(await instance.redemptionActive(), false);
		});

		it('should access its dependencies via the address resolver', async () => {
			assert.equal(await addressResolver.getAddress(toBytes32('Issuer')), issuer.address);
			assert.equal(
				await addressResolver.getAddress(toBytes32('ExchangeRates')),
				exchangeRates.address
			);
		});
	});

	describe('suspendRedemption', () => {
		describe('failure modes', () => {
			beforeEach(async () => {
				// first resume redemptions
				await instance.resumeRedemption({ from: owner });
			});

			it('reverts when not invoked by the owner', async () => {
				await onlyGivenAddressCanInvoke({
					fnc: instance.suspendRedemption,
					args: [],
					accounts,
					reason: 'Only the contract owner may perform this action',
					address: owner,
				});
			});

			it('reverts when redemption is already suspended', async () => {
				await instance.suspendRedemption({ from: owner });
				await assert.revert(instance.suspendRedemption({ from: owner }), 'Redemption suspended');
			});
		});

		describe('when invoked by the owner', () => {
			let txn;
			beforeEach(async () => {
				// first resume redemptions
				await instance.resumeRedemption({ from: owner });
				txn = await instance.suspendRedemption({ from: owner });
			});

			it('and redemptionActive is false', async () => {
				assert.equal(await instance.redemptionActive(), false);
			});

			it('and a RedemptionSuspended event is emitted', async () => {
				assert.eventEqual(txn, 'RedemptionSuspended', []);
			});
		});
	});

	describe('resumeRedemption', () => {
		describe('failure modes', () => {
			it('reverts when not invoked by the owner', async () => {
				await onlyGivenAddressCanInvoke({
					fnc: instance.resumeRedemption,
					args: [],
					accounts,
					reason: 'Only the contract owner may perform this action',
					address: owner,
				});
			});

			it('reverts when redemption is not suspended', async () => {
				await instance.resumeRedemption({ from: owner });
				await assert.revert(instance.resumeRedemption({ from: owner }), 'Redemption not suspended');
			});
		});

		describe('when redemption is suspended', () => {
			it('redemptionActive is false', async () => {
				assert.equal(await instance.redemptionActive(), false);
			});

			describe('when invoked by the owner', () => {
				let txn;
				beforeEach(async () => {
					txn = await instance.resumeRedemption({ from: owner });
				});

				it('redemptions are active again', async () => {
					assert.equal(await instance.redemptionActive(), true);
				});

				it('a RedemptionResumed event is emitted', async () => {
					assert.eventEqual(txn, 'RedemptionResumed', []);
				});
			});
		});
	});

	describe('setDiscountRate()', () => {
		it('may only be called by the owner', async () => {
			await onlyGivenAddressCanInvoke({
				fnc: instance.setDiscountRate,
				args: [toUnit('1.0')],
				accounts,
				address: owner,
				reason: 'Only the contract owner may perform this action',
			});
		});

		it('may not set a rate greater than 1', async () => {
			await assert.revert(
				instance.setDiscountRate(toUnit('1.000001'), { from: owner }),
				'Invalid rate'
			);
		});
	});

	describe('redemption', () => {
		const redeemAmount = toUnit('100.0');

		beforeEach(async () => {
			// first wrap ETH using wrapper to get sETH
			await weth.deposit({ from: account1, value: redeemAmount });
			await weth.approve(etherWrapper.address, redeemAmount, { from: account1 });
			await etherWrapper.mint(redeemAmount, { from: account1 });
		});

		beforeEach(async () => {
			await instance.resumeRedemption({ from: owner });
		});

		describe('redeem()', () => {
			it('reverts when redemption is suspended', async () => {
				await instance.suspendRedemption({ from: owner });
				await assert.revert(
					instance.redeem(proxysETH.address, {
						from: account1,
					}),
					'Redemption deactivated'
				);
			});

			it('reverts when discount rate is set to zero', async () => {
				await instance.setDiscountRate(toUnit('0'), { from: owner });
				await assert.revert(
					instance.redeem(proxysETH.address, {
						from: account1,
					}),
					'Synth not redeemable'
				);
			});

			it('reverts when user has no balance', async () => {
				await assert.revert(
					instance.redeem(proxysBTC.address, {
						from: account1,
					}),
					'No balance of synth to redeem'
				);
			});

			it('reverts when user attempts to redeem sUSD', async () => {
				await assert.revert(
					instance.redeem(proxysUSD.address, {
						from: account1,
					}),
					'Cannot redeem sUSD'
				);
			});

			it('reverts when user attempts to redeem a non-synth token', async () => {
				await assert.revert(
					instance.redeem(proxySynthetix.address, {
						from: account1,
					})
				);
			});

			describe('when the user has a synth balance', () => {
				describe('when redeem is called by the user', () => {
					let txn;
					beforeEach(async () => {
						txn = await instance.redeem(proxysETH.address, { from: account1 });
					});
					it('emits a SynthRedeemed event', async () => {
						assert.eventEqual(txn, 'SynthRedeemed', {
							synth: proxysETH.address,
							account: account1,
							amountOfSynth: redeemAmount,
							amountInsUSD: toUnit('350000'), // 100 sETH redeemed at price of $3500 is 350,000 sUSD
						});
					});
				});
			});
		});
		describe('redeemAll()', () => {
			it('reverts when redemption is suspended', async () => {
				await instance.suspendRedemption({ from: owner });
				await assert.revert(
					instance.redeemAll([proxysBTC.address, proxysETH.address], {
						from: account1,
					}),
					'Redemption deactivated'
				);
			});

			it('reverts when neither synths are redeemable', async () => {
				await updateAggregatorRates(
					exchangeRates,
					null,
					[sBTC, sETH, ETH],
					['0', '0', '0'].map(toUnit)
				);

				await assert.revert(
					instance.redeemAll([proxysBTC.address, proxysETH.address], {
						from: account1,
					}),
					'Synth not redeemable'
				);
			});

			describe('when redemption is active', () => {
				describe('when redeemAll is called by the user for both synths', () => {
					let sBTCBalance, sETHBalance;
					beforeEach(async () => {
						sBTCBalance = await proxysBTC.balanceOf(account1);
						sETHBalance = await proxysETH.balanceOf(account1);
					});
					it('reverts when user only has a balance of one synth', async () => {
						assert.bnEqual(sBTCBalance, 0);
						assert.bnEqual(sETHBalance, redeemAmount);
						await assert.revert(
							instance.redeemAll([proxysBTC.address, proxysETH.address], {
								from: account1,
							}),
							'No balance of synth to redeem'
						);
					});
					describe('when user has balances for both synths', () => {
						const exchangeAmount = toUnit('10');
						const expectedAmountBTC = toUnit('0.5');

						let txn;
						beforeEach(async () => {
							await synthetix.exchange(sETH, exchangeAmount, sBTC, { from: account1 });

							txn = await instance.redeemAll([proxysBTC.address, proxysETH.address], {
								from: account1,
							});
						});

						it('transfers the correct amount of sUSD to the user', async () => {
							assert.bnEqual(await proxysBTC.balanceOf(account1), 0);
							assert.bnEqual(await proxysETH.balanceOf(account1), 0);
							assert.bnEqual(await proxysUSD.balanceOf(account1), toUnit('350000'));
						});

						it('emits a SynthRedeemed event for each synth', async () => {
							assert.eventEqual(txn.logs[0], 'SynthRedeemed', {
								synth: proxysBTC.address,
								account: account1,
								amountOfSynth: expectedAmountBTC,
								amountInsUSD: multiplyDecimal(expectedAmountBTC, priceBTC),
							});

							assert.eventEqual(txn.logs[1], 'SynthRedeemed', {
								synth: proxysETH.address,
								account: account1,
								amountOfSynth: sETHBalance.sub(exchangeAmount),
								amountInsUSD: multiplyDecimal(sETHBalance.sub(exchangeAmount), priceETH),
							});
						});
					});
				});
			});
		});
		describe('redeemPartial()', () => {
			const partialAmount = toUnit('25.0');

			it('reverts when redemption is suspended', async () => {
				await instance.suspendRedemption({ from: owner });
				await assert.revert(
					instance.redeemPartial(proxysETH.address, partialAmount, {
						from: account1,
					}),
					'Redemption deactivated'
				);
			});

			describe('when redemption is active', () => {
				describe('when redeemPartial is called by the user', () => {
					let sETHBalance;
					beforeEach(async () => {
						sETHBalance = await proxysETH.balanceOf(account1);
					});
					it('reverts when user does not have enough balance', async () => {
						assert.bnEqual(sETHBalance, redeemAmount);
						await assert.revert(
							instance.redeemPartial(proxysETH.address, redeemAmount.add(partialAmount), {
								from: account1,
							}),
							'Insufficient balance'
						);
					});
					describe('when user has enough balance', () => {
						let txn;
						beforeEach(async () => {
							txn = await instance.redeemPartial(proxysETH.address, partialAmount, {
								from: account1,
							});
						});

						it('transfers the correct amount of sUSD to the user', async () => {
							assert.bnEqual(await proxysETH.balanceOf(account1), redeemAmount.sub(partialAmount));
							assert.bnEqual(await proxysUSD.balanceOf(account1), toUnit('87500'));
						});

						it('emits a SynthRedeemed event with the partial amount', async () => {
							assert.eventEqual(txn, 'SynthRedeemed', {
								synth: proxysETH.address,
								account: account1,
								amountOfSynth: partialAmount,
								amountInsUSD: multiplyDecimal(partialAmount, priceETH),
							});
						});
					});
				});
			});
		});
	});
});
