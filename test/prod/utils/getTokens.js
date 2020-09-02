const { connectContract } = require('./connectContract');
const { toBytes32 } = require('../../../index.js');
const { web3 } = require('@nomiclabs/buidler');
const { toBN } = web3.utils;

async function getEther({ amount, account, fromAccount }) {
	const balance = toBN(await web3.eth.getBalance(fromAccount));
	if (balance.lt(amount)) {
		throw new Error(
			`Account ${fromAccount} only has ${balance} ETH and cannot transfer ${amount} ETH to ${account} `
		);
	}

	await web3.eth.sendTransaction({
		from: fromAccount,
		to: account,
		value: amount,
	});
}

async function getSNX({ network, amount, account, fromAccount }) {
	const SNX = await connectContract({ network, contractName: 'ProxyERC20' });

	const balance = toBN(await SNX.balanceOf(fromAccount));
	if (balance.lt(amount)) {
		throw new Error(
			`Account ${fromAccount} only has ${balance} SNX and cannot transfer ${amount} SNX to ${account} `
		);
	}

	await SNX.transfer(account, amount, {
		from: fromAccount,
	});
}

async function getsUSD({ network, amount, account, fromAccount }) {
	const sUSD = await connectContract({ network, contractName: 'SynthsUSD', abiName: 'Synth' });
	const balance = toBN(await sUSD.transferableSynths(fromAccount));

	if (balance.lt(amount)) {
		const snxToTransfer = amount.mul(toBN('10'));
		await getSNX({ network, account, amount: snxToTransfer, fromAccount });

		const Synthetix = await connectContract({
			network,
			contractName: 'ProxyERC20',
			abiName: 'Synthetix',
		});

		await Synthetix.issueSynths(amount, {
			from: account,
		});
	} else {
		await sUSD.transferAndSettle(account, amount, { from: fromAccount });
	}
}

module.exports = {
	getEther,
	getsUSD,
	getSNX,
};
