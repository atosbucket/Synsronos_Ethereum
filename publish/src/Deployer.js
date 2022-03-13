'use strict';

const linker = require('solc/linker');
const ethers = require('ethers');
const { gray, green, yellow } = require('chalk');
const fs = require('fs');
const { stringify, getExplorerLinkPrefix, assignGasOptions } = require('./util');
const { getVersions, getUsers } = require('../..');

class Deployer {
	/**
	 *
	 * @param {object} compiled An object with full combined contract name keys mapping to ABIs and bytecode
	 * @param {object} config An object with full combined contract name keys mapping to a deploy flag and the contract source file name
	 * @param {object} deployment An object with full combined contract name keys mapping to existing deployment addresses (if any)
	 */
	constructor({
		compiled,
		config,
		configFile,
		deployment,
		deploymentFile,
		dryRun,
		gasPrice,
		maxFeePerGas,
		maxPriorityFeePerGas,
		network,
		providerUrl,
		privateKey,
		useFork,
		useOvm,
		nonceManager,
	}) {
		this.compiled = compiled;
		this.config = config;
		this.configFile = configFile;
		this.deployment = deployment;
		this.deploymentFile = deploymentFile;
		this.dryRun = dryRun;
		this.gasPrice = gasPrice;
		this.maxFeePerGas = maxFeePerGas;
		this.maxPriorityFeePerGas = maxPriorityFeePerGas;
		this.network = network;
		this.nonceManager = nonceManager;
		this.useOvm = useOvm;

		this.provider = new ethers.providers.JsonRpcProvider(providerUrl);

		// use the default owner when in a fork or in local mode and no private key supplied
		if ((useFork || network === 'local') && !privateKey) {
			const ownerAddress = getUsers({ network, useOvm, user: 'owner' }).address;
			this.signer = this.provider.getSigner(ownerAddress);
			this.signer.address = ownerAddress;
		} else {
			this.signer = new ethers.Wallet(privateKey, this.provider);
		}
		this.account = this.signer.address;
		this.deployedContracts = {};
		this.replacedContracts = {};
		this._dryRunCounter = 0;

		// Updated Config (Make a copy, don't mutate original)
		this.updatedConfig = JSON.parse(JSON.stringify(config));

		// Keep track of newly deployed contracts
		this.newContractsDeployed = [];
	}

	async evaluateNextDeployedContractAddress() {
		const nonce = await this.provider.getTransactionCount(this.account);
		const rlpEncoded = ethers.utils.RLP.encode([this.account, ethers.utils.hexlify(nonce)]);
		const hashed = ethers.utils.keccak256(rlpEncoded);

		return `0x${hashed.slice(12).substring(14)}`;
	}

	checkBytesAreSafeForOVM(bytes) {
		for (let i = 0; i < bytes.length; i += 2) {
			const curByte = bytes.substr(i, 2);
			const opNum = parseInt(curByte, 16);

			// opNum is >=0x60 and <0x80
			if (opNum >= 96 && opNum < 128) {
				i += 2 * (opNum - 95); // For PUSH##, OpNum - 0x5f = ##
				continue;
			}

			if (curByte === '5b') {
				return false;
			}
		}

		return true;
	}

	getEncodedDeploymentParameters({ abi, params }) {
		const constructorABI = abi.find(item => item.type === 'constructor');
		if (!constructorABI) {
			return '0x';
		}

		const inputs = constructorABI.inputs;
		if (!inputs || inputs.length === 0) {
			return '0x';
		}

		const types = inputs.map(input => input.type);
		return ethers.utils.defaultAbiCoder.encode(types, params);
	}

	async sendDummyTx() {
		const tx = await assignGasOptions({
			tx: {
				to: '0x0000000000000000000000000000000000000001',
				data: '0x0000000000000000000000000000000000000000000000000000000000000000',
				value: 0,
			},
			provider: this.provider,
			maxFeePerGas: this.maxFeePerGas,
			maxPriorityFeePerGas: this.maxPriorityFeePerGas,
		});

		const response = await this.signer.sendTransaction(tx);
		await response.wait();

		if (this.nonceManager) {
			this.nonceManager.incrementNonce();
		}
	}

	async sendOverrides() {
		const params = await assignGasOptions({
			tx: {},
			provider: this.provider,
			maxFeePerGas: this.maxFeePerGas,
			maxPriorityFeePerGas: this.maxPriorityFeePerGas,
		});

		if (this.nonceManager) {
			params.nonce = await this.nonceManager.getNonce();
		}

		return params;
	}

	async _deploy({ name, source, args = [], deps = [], force = false, dryRun = this.dryRun }) {
		if (!this.config[name] && !force) {
			console.log(yellow(`Skipping ${name} as it is NOT in contract flags file for deployment.`));
			return;
		}
		const missingDeps = deps.filter(d => !this.deployedContracts[d] && !this.deployment.targets[d]);
		if (missingDeps.length) {
			throw Error(`Cannot deploy ${name} as it is missing dependencies: ${missingDeps.join(',')}`);
		}
		// by default, we deploy if force tells us to
		let deploy = force;
		// though use what's in the config if it exists
		if (this.config[name]) {
			deploy = this.config[name].deploy;
		}

		const existingAddress = this.deployment.targets[name]
			? this.deployment.targets[name].address
			: '';
		const existingSource = this.deployment.targets[name]
			? this.deployment.targets[name].source
			: '';
		const existingABI = this.deployment.sources[existingSource]
			? this.deployment.sources[existingSource].abi
			: '';

		let deployedContract;

		if (deploy) {
			// if deploying, do check of compiled sources
			const compiled = this.compiled[source];

			if (!compiled) {
				throw new Error(
					`No compiled source for: ${name}. The source file is set to ${source}.sol - is that correct?`
				);
			}

			// Any contract after SafeDecimalMath can automatically get linked.
			// Doing this with bytecode that doesn't require the library is a no-op.
			let bytecode = compiled.evm.bytecode.object;
			['SafeDecimalMath', 'Math', 'SystemSettingsLib'].forEach(contractName => {
				if (this.deployedContracts[contractName]) {
					bytecode = linker.linkBytecode(bytecode, {
						[source + '.sol']: {
							[contractName]: this.deployedContracts[contractName].address,
						},
					});
				}
			});

			compiled.evm.bytecode.linkedObject = bytecode;
			console.log(
				gray(` - Attempting to deploy ${name}${name !== source ? ` (with source ${source})` : ''}`)
			);
			let gasUsed;
			if (dryRun) {
				this._dryRunCounter++;
				// use the existing version of a contract in a dry run, but deep clone it using JSON stringify
				// to prevent issues with ethers and readonly
				deployedContract = JSON.parse(
					JSON.stringify(this.makeContract({ abi: compiled.abi, address: existingAddress }))
				);
				const { account } = this;
				// but stub out all method calls except owner because it is needed to
				// determine which actions can be performed directly or need to be added to ownerActions
				Object.keys(deployedContract.functions).forEach(key => {
					deployedContract.functions[key] = () => ({
						call: () =>
							key === 'owner'
								? Promise.resolve(account)
								: key === 'resolverAddressesRequired'
								? Promise.resolve([])
								: undefined,
					});
				});
				deployedContract.address = '0x' + this._dryRunCounter.toString().padStart(40, '0');
			} else {
				const factory = new ethers.ContractFactory(compiled.abi, bytecode, this.signer);

				const overrides = await this.sendOverrides();

				deployedContract = await factory.deploy(...args, overrides);
				const receipt = await deployedContract.deployTransaction.wait();

				gasUsed = receipt.gasUsed;

				if (this.nonceManager) {
					this.nonceManager.incrementNonce();
				}
			}
			deployedContract.justDeployed = true; // indicate a fresh deployment occurred

			if (existingAddress && existingABI) {
				// keep track of replaced contract in case required (useful when doing local )
				this.replacedContracts[name] = this.makeContract({
					abi: existingABI,
					address: existingAddress,
				});
				this.replacedContracts[name].source = existingSource;
			}

			// Deployment in OVM could result in empty bytecode if
			// the contract's constructor parameters are unsafe.
			// This check is probably redundant given the previous check, but just in case...
			if (this.useOvm && !dryRun) {
				const code = await this.provider.getCode(deployedContract.address);

				if (code.length === 2) {
					throw new Error(`Contract deployment resulted in a contract with no bytecode: ${code}`);
				}
			}

			console.log(
				green(
					`${dryRun ? '[DRY RUN] - Simulated deployment of' : '- Deployed'} ${name} to ${
						deployedContract.address
					} ${gasUsed ? `used ${(gasUsed / 1e6).toFixed(1)}m in gas` : ''}`
				)
			);
			// track the source file for potential usage
			deployedContract.source = source;
		} else if (existingAddress && existingABI) {
			// get ABI from the deployment (not the compiled ABI which may be newer)
			deployedContract = this.makeContract({ abi: existingABI, address: existingAddress });
			console.log(gray(` - Reusing instance of ${name} at ${existingAddress}`));
			deployedContract.source = existingSource;
		} else {
			throw new Error(
				`Settings for contract: ${name} specify an existing contract, but cannot find address or ABI.`
			);
		}

		// append new deployedContract
		this.deployedContracts[name] = deployedContract;

		return deployedContract;
	}

	async _updateResults({ name, source, deployed, address }) {
		let timestamp = new Date();
		let txn = '';
		if (this.config[name] && !this.config[name].deploy) {
			// deploy is false, so we reused a deployment, thus lets grab the details that already exist
			timestamp = this.deployment.targets[name].timestamp;
			txn = this.deployment.targets[name].txn;
		}
		const { network, useOvm } = this;
		// now update the deployed contract information
		this.deployment.targets[name] = {
			name,
			address,
			source,
			link: `${getExplorerLinkPrefix({ network, useOvm })}/address/${
				this.deployedContracts[name].address
			}`,
			timestamp,
			txn,
			network: this.network,
		};
		if (deployed) {
			// remove the output from the metadata (don't dupe the ABI)
			delete this.compiled[source].metadata.output;

			// track the new source and bytecode
			this.deployment.sources[source] = {
				bytecode: this.compiled[source].evm.bytecode.object,
				abi: this.compiled[source].abi,
				source: Object.values(this.compiled[source].metadata.sources)[0],
				metadata: this.compiled[source].metadata,
			};
			// add to the list of deployed contracts for later reporting
			this.newContractsDeployed.push({
				name,
				address,
			});
		}
		if (!this.dryRun) {
			fs.writeFileSync(this.deploymentFile, stringify(this.deployment));
		}

		// now update the flags to indicate it no longer needs deployment,
		// ignoring this step for local, which wants a full deployment by default
		if (this.configFile && this.network !== 'local' && !this.dryRun) {
			this.updatedConfig[name] = { deploy: false };
			fs.writeFileSync(this.configFile, stringify(this.updatedConfig));
		}
	}

	async deployContract({
		name,
		library = false,
		skipResolver = false,
		source = name,
		args = [],
		deps = [],
		force = false,
		dryRun = this.dryRun,
	}) {
		const forbiddenAddress = (this.deployedContracts['AddressResolver'] || {}).address;
		for (const arg of args) {
			if (
				forbiddenAddress &&
				typeof arg === 'string' &&
				arg.toLowerCase() === forbiddenAddress.toLowerCase()
			) {
				throw Error(
					`new ${name}(): Cannot use the AddressResolver as a constructor arg. Use ReadProxyAddressResolver instead.`
				);
			}
		}

		// Deploys contract according to configuration
		const deployedContract = await this._deploy({
			name,
			source,
			args,
			deps,
			force,
			dryRun,
		});

		if (!deployedContract) {
			return;
		}

		deployedContract.library = library;
		deployedContract.skipResolver = skipResolver;

		// Updates `config.json` and `deployment.json`, as well as to
		// the local variable newContractsDeployed
		await this._updateResults({
			name,
			source: deployedContract.source,
			deployed: deployedContract.justDeployed,
			address: deployedContract.address,
		});

		return deployedContract;
	}

	makeContract({ abi, address }) {
		return new ethers.Contract(address, abi, this.signer);
	}

	getExistingContract({ contract }) {
		let address;
		if (this.network === 'local') {
			// try find the last replaced contract
			// Note: this stores it in memory, so only really useful for
			// local mode as when doing a real deploy we need to handle
			// broken and resumed deploys
			({ address } = this.replacedContracts[contract]
				? this.replacedContracts[contract]
				: this.deployment.targets[contract]);
		} else {
			const contractVersion = getVersions({
				network: this.network,
				useOvm: this.useOvm,
				byContract: true,
			})[contract];
			const lastEntry = contractVersion.slice(-1)[0];
			address = lastEntry.address;
		}

		const { source } = this.deployment.targets[contract];
		const { abi } = this.deployment.sources[source];
		return this.makeContract({ abi, address });
	}
}

module.exports = Deployer;
