from utils.deployutils import (
    W3, UNIT, MASTER, DUMMY,
    fresh_account, fresh_accounts,
    mine_tx, attempt_deploy, mine_txs,
    take_snapshot, restore_snapshot
)
from utils.testutils import (
    HavvenTestCase, ZERO_ADDRESS,
)
from tests.contract_interfaces.nomin_interface import PublicNominInterface
from tests.contract_interfaces.havven_interface import HavvenInterface
from tests.contract_interfaces.court_interface import FakeCourtInterface


def setUpModule():
    print("Testing Nomin...")
    print("================")
    print()


def tearDownModule():
    print()
    print()


class TestNomin(HavvenTestCase):
    def setUp(self):
        self.snapshot = take_snapshot()

    def tearDown(self):
        restore_snapshot(self.snapshot)

    @classmethod
    def deployContracts(cls):
        sources = ["tests/contracts/PublicNomin.sol", "tests/contracts/FakeCourt.sol", "contracts/Havven.sol"]

        compiled, cls.event_maps = cls.compileAndMapEvents(sources)

        havven_proxy, _ = attempt_deploy(compiled, 'Proxy', MASTER, [MASTER])
        nomin_proxy, _ = attempt_deploy(compiled, 'Proxy', MASTER, [MASTER])
        proxied_havven = W3.eth.contract(address=havven_proxy.address, abi=compiled['Havven']['abi'])
        proxied_nomin = W3.eth.contract(address=nomin_proxy.address, abi=compiled['PublicNomin']['abi'])

        nomin_contract, _ = attempt_deploy(
            compiled, 'PublicNomin', MASTER, [nomin_proxy.address, MASTER, MASTER]
        )

        havven_contract, _ = attempt_deploy(
            compiled, "Havven", MASTER, [havven_proxy.address, ZERO_ADDRESS, MASTER, MASTER, UNIT//2]
        )

        fake_court, _ = attempt_deploy(compiled, 'FakeCourt', MASTER, [])

        mine_txs([
            havven_proxy.functions.setTarget(havven_contract.address).transact({'from': MASTER}),
            nomin_proxy.functions.setTarget(nomin_contract.address).transact({'from': MASTER}),
            havven_contract.functions.setNomin(nomin_contract.address).transact({'from': MASTER}),
            nomin_contract.functions.setCourt(fake_court.address).transact({'from': MASTER}),
            nomin_contract.functions.setHavven(havven_contract.address).transact({'from': MASTER})
        ])

        return havven_proxy, proxied_havven, nomin_proxy, proxied_nomin, nomin_contract, havven_contract, fake_court

    @classmethod
    def setUpClass(cls):
        cls.havven_proxy, cls.proxied_havven, cls.nomin_proxy, cls.proxied_nomin, cls.nomin_contract, cls.havven_contract, cls.fake_court_contract = cls.deployContracts()

        cls.nomin_event_dict = cls.event_maps['Nomin']

        cls.nomin = PublicNominInterface(cls.proxied_nomin, "Nomin")
        cls.havven = HavvenInterface(cls.proxied_havven, "Havven")

        cls.fake_court = FakeCourtInterface(cls.fake_court_contract, "FakeCourt")

        cls.fake_court.setNomin(MASTER, cls.nomin_contract.address)

        cls.nomin.setFeeAuthority(MASTER, cls.havven_contract.address)

    def test_constructor(self):
        # Nomin-specific members
        self.assertEqual(self.nomin.owner(), MASTER)
        self.assertTrue(self.nomin.frozen(self.nomin_contract.address))

        # ExternStateFeeToken members
        self.assertEqual(self.nomin.name(), "USD Nomins")
        self.assertEqual(self.nomin.symbol(), "nUSD")
        self.assertEqual(self.nomin.totalSupply(), 0)
        self.assertEqual(self.nomin.balanceOf(MASTER), 0)
        self.assertEqual(self.nomin.transferFeeRate(), 15 * UNIT // 10000)
        self.assertEqual(self.nomin.feeAuthority(), self.nomin.havven())
        self.assertEqual(self.nomin.decimals(), 18)

    def test_setOwner(self):
        pre_owner = self.nomin.owner()
        new_owner = DUMMY

        # Only the owner must be able to set the owner.
        self.assertReverts(self.nomin.nominateNewOwner, new_owner, new_owner)
        txr = mine_tx(self.nomin_contract.functions.nominateNewOwner(new_owner).transact({'from': pre_owner}), 'nominateNewOwner', 'Nomin')
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'OwnerNominated',
            fields={'newOwner': new_owner},
            location=self.nomin_contract.address
        )
        txr = mine_tx(self.nomin_contract.functions.acceptOwnership().transact({'from': new_owner}), 'acceptOwnership', 'Nomin')
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'OwnerChanged',
            fields={'oldOwner': pre_owner, 'newOwner': new_owner},
            location=self.nomin_contract.address
        )
        self.assertEqual(self.nomin_contract.functions.owner().call(), new_owner)

    def test_setCourt(self):
        new_court = DUMMY
        old_court = self.nomin.court()

        # Only the owner must be able to set the court.
        txr = self.nomin.setCourt(self.nomin.owner(), new_court)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'CourtUpdated',
            fields={'newCourt': new_court},
            location=self.nomin_proxy.address
        )
        self.assertEqual(self.nomin.court(), new_court)
        self.assertReverts(self.nomin.setCourt, DUMMY, new_court)
        self.nomin.setCourt(self.nomin.owner(), old_court)

    def test_setHavven(self):
        new_havven = DUMMY
        old_havven = self.nomin.havven()

        # Only the owner must be able to set the court.
        txr = self.nomin.setHavven(self.nomin.owner(), new_havven)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'FeeAuthorityUpdated',
            fields={'newFeeAuthority': new_havven},
            location=self.nomin_proxy.address
        )
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'HavvenUpdated',
            fields={'newHavven': new_havven},
            location=self.nomin_proxy.address
        )
        self.assertEqual(self.nomin.havven(), new_havven)
        self.assertReverts(self.nomin.setHavven, DUMMY, old_havven)
        self.nomin.setHavven(self.nomin.owner(), old_havven)

    def test_ensureCompleteTransfer(self):
        amount = 10 * UNIT
        amountToSend = amount / 1.0015
        fee = amount - amountToSend
        sender = fresh_account()
        receiver = fresh_account()

        # Give them the nomins to start the test
        self.nomin.giveNomins(MASTER, sender, amount)
        self.assertEqual(self.nomin.balanceOf(sender), amount)

        # Transfer the amount to another account
        txr = self.nomin.transfer(sender, receiver, amount)
        
        # Ensure the result of the transfer is correct.
        self.assertEqual(self.nomin.balanceOf(sender), 0)
        self.assertEqual(self.nomin.balanceOf(receiver), amount - fee)
        self.assertEqual(self.nomin.feePool(), fee)

    def test_transferEventEmits(self):
        amount = 5 * UNIT
        fee = amount * 0.0015
        sender = fresh_account()
        receiver = fresh_account()

        # Give them the nomins to start the test
        self.nomin.giveNomins(MASTER, sender, amount)
        self.assertEqual(self.nomin.balanceOf(sender), amount)

        # Transfer the amount to another account
        txr = self.nomin.transfer(sender, receiver, amount)

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'Transfer',
            fields={'from': sender, 'to': receiver, 'value': 5 * UNIT - fee},
            location=self.nomin_proxy.address
        )

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Transfer',
            fields={
                'from': sender,
                'to': self.nomin_contract.address,
                'value': fee
            },
            location=self.nomin_proxy.address
        )

    def test_transfer(self):
        target = fresh_account()

        self.nomin.giveNomins(MASTER, MASTER, 10 * UNIT)
        self.assertEqual(self.nomin.balanceOf(MASTER), 10 * UNIT)
        self.assertEqual(self.nomin.balanceOf(target), 0)

        # Should be impossible to transfer to the nomin contract itself.
        self.assertReverts(self.nomin.transfer, MASTER, self.nomin_contract.address, UNIT)

        txr = self.nomin.transfer(MASTER, target, 5 * UNIT)

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'Transfer',
            fields={'from': MASTER, 'to': target, 'value': self.nomin.priceToSpend(5*UNIT)},
            location=self.nomin_proxy.address
        )

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Transfer',
            fields={
                'from': MASTER,
                'to': self.nomin_contract.address,
                'value': self.nomin.transferFeeIncurred(self.nomin.priceToSpend(5*UNIT))
            },
            location=self.nomin_proxy.address
        )

        self.assertClose(self.nomin.balanceOf(MASTER), 5 * UNIT)
        self.assertEqual(self.nomin.balanceOf(target), self.nomin.priceToSpend(5 * UNIT))
        self.assertEqual(self.nomin.feePool(), self.nomin.transferFeeIncurred(self.nomin.priceToSpend(5 * UNIT)))

        txr = self.nomin.debugFreezeAccount(MASTER, target)

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'AccountFrozen',
            fields={'target': target, 'balance': self.nomin.priceToSpend(5 * UNIT)},
            location=self.nomin_proxy.address
        )

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Transfer',
            fields={
                'from': target,
                'to': self.nomin_contract.address,
                'value': self.nomin.priceToSpend(5 * UNIT)
            },
            location=self.nomin_proxy.address
        )

        self.assertEqual(self.nomin.balanceOf(target), 0)

        self.assertReverts(self.nomin.transfer, MASTER, target, UNIT)
        self.assertReverts(self.nomin.transfer, target, MASTER, UNIT)

        txr = self.nomin.unfreezeAccount(MASTER, target)

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'AccountUnfrozen',
            fields={'target': target},
            location=self.nomin_proxy.address
        )
        self.assertEqual(self.nomin.balanceOf(target), 0)

        txr = self.nomin.transfer(MASTER, target, 5 * UNIT)

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'Transfer',
            fields={'from': MASTER, 'to': target, 'value': self.nomin.priceToSpend(5*UNIT)},
            location=self.nomin_proxy.address
        )

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Transfer',
            fields={
                'from': MASTER,
                'to': self.nomin_contract.address,
                'value': self.nomin.transferFeeIncurred(self.nomin.priceToSpend(5*UNIT))
            },
            location=self.nomin_proxy.address
        )

        self.assertEqual(self.nomin.balanceOf(target), self.nomin.priceToSpend(5 * UNIT))
        self.assertLess(self.nomin.balanceOf(MASTER), 3)  # assert MASTER only has the tiniest bit of change

    def test_transferFrom(self):
        target = fresh_account()

        self.nomin.giveNomins(MASTER, MASTER, 10 * UNIT)

        # Unauthorized transfers should not work
        self.assertReverts(self.nomin.transferFrom, DUMMY, MASTER, target, UNIT)

        # Neither should transfers that are too large for the allowance.
        txr = self.nomin.approve(MASTER, DUMMY, UNIT)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'Approval',
            fields={'owner': MASTER, 'spender': DUMMY, 'value': UNIT},
            location=self.nomin_proxy.address
        )

        self.assertReverts(self.nomin.transferFrom, DUMMY, MASTER, target, 2 * UNIT)

        txr = self.nomin.approve(MASTER, DUMMY, 10000 * UNIT)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'Approval',
            fields={'owner': MASTER, 'spender': DUMMY, 'value': 10000 * UNIT},
            location=self.nomin_proxy.address
        )

        self.assertEqual(self.nomin.balanceOf(MASTER), 10 * UNIT)
        self.assertEqual(self.nomin.balanceOf(target), 0)

        # Should be impossible to transfer to the nomin contract itself.
        self.assertReverts(self.nomin.transferFrom, DUMMY, MASTER, self.nomin_contract.address, UNIT)

        txr = self.nomin.transferFrom(DUMMY, MASTER, target, 5 * UNIT)

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'Transfer',
            fields={'from': MASTER, 'to': target, 'value': self.nomin.priceToSpend(5 * UNIT)},
            location=self.nomin_proxy.address
        )

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Transfer',
            fields={
                'from': MASTER,
                'to': self.nomin_contract.address,
                'value': self.nomin.transferFeeIncurred(self.nomin.priceToSpend(5 * UNIT))
            },
            location=self.nomin_proxy.address
        )

        self.assertClose(self.nomin.balanceOf(MASTER), 5 * UNIT)
        self.assertEqual(self.nomin.balanceOf(target), self.nomin.priceToSpend(5 * UNIT))
        self.assertEqual(self.nomin.feePool(), self.nomin.transferFeeIncurred(self.nomin.priceToSpend(5 * UNIT)))

        txr = self.nomin.debugFreezeAccount(MASTER, target)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'AccountFrozen',
            fields={'target': target, 'balance': self.nomin.priceToSpend(5 * UNIT)},
            location=self.nomin_proxy.address
        )

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Transfer',
            fields={
                'from': target,
                'to': self.nomin_contract.address,
                'value': self.nomin.priceToSpend(5 * UNIT)
            },
            location=self.nomin_proxy.address
        )

        self.assertReverts(self.nomin.transferFrom, DUMMY, MASTER, target, UNIT)
        self.assertReverts(self.nomin.transferFrom, DUMMY, target, MASTER, UNIT)

        txr = self.nomin.unfreezeAccount(MASTER, target)

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'AccountUnfrozen',
            fields={'target': target},
            location=self.nomin_proxy.address
        )

        txr = self.nomin.transferFrom(DUMMY, MASTER, target, 5 * UNIT)

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'Transfer',
            fields={'from': MASTER, 'to': target, 'value': self.nomin.priceToSpend(5 * UNIT)},
            location=self.nomin_proxy.address
        )

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Transfer',
            fields={
                'from': MASTER,
                'to': self.nomin_contract.address,
                'value': self.nomin.transferFeeIncurred(self.nomin.priceToSpend(5 * UNIT))
            },
            location=self.nomin_proxy.address
        )

        self.assertEqual(self.nomin.balanceOf(target), self.nomin.priceToSpend(5 * UNIT))
        self.assertLess(self.nomin.balanceOf(MASTER), 3)  # assert MASTER only has the tiniest bit of change

    def test_transferSenderPaysFee(self):
        target = fresh_account()

        self.nomin.giveNomins(MASTER, MASTER, 10 * UNIT)
        self.assertEqual(self.nomin.balanceOf(MASTER), 10 * UNIT)
        self.assertEqual(self.nomin.balanceOf(target), 0)

        # Should be impossible to transfer to the nomin contract itself.
        self.assertReverts(self.nomin.transfer, MASTER, self.nomin_contract.address, UNIT)

        txr = self.nomin.transferSenderPaysFee(MASTER, target, 5 * UNIT)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'Transfer',
            fields={'from': MASTER, 'to': target, 'value': 5 * UNIT},
            location=self.nomin_proxy.address
        )

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Transfer',
            fields={
                'from': MASTER,
                'to': self.nomin_contract.address,
                'value': self.nomin.transferFeeIncurred(5 * UNIT)
            },
            location=self.nomin_proxy.address
        )

        self.assertEqual(self.nomin.balanceOf(MASTER), 5 * UNIT - self.nomin.transferFeeIncurred(5 * UNIT))
        self.assertEqual(self.nomin.balanceOf(target), 5 * UNIT)
        self.assertEqual(self.nomin.feePool(), self.nomin.transferFeeIncurred(5 * UNIT))

        txr = self.nomin.debugFreezeAccount(MASTER, target)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'AccountFrozen',
            fields={'target': target, 'balance': 5 * UNIT},
            location=self.nomin_proxy.address
        )

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Transfer',
            fields={
                'from': target,
                'to': self.nomin_contract.address,
                'value': 5 * UNIT
            },
            location=self.nomin_proxy.address
        )

        self.assertEqual(self.nomin.balanceOf(target), 0)

        self.assertReverts(self.nomin.transfer, MASTER, target, UNIT)
        self.assertReverts(self.nomin.transfer, target, MASTER, UNIT)

        txr = self.nomin.unfreezeAccount(MASTER, target)

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'AccountUnfrozen',
            fields={'target': target},
            location=self.nomin_proxy.address
        )
        self.assertEqual(self.nomin.balanceOf(target), 0)

        old_bal = self.nomin.balanceOf(MASTER)

        txr = self.nomin.transferSenderPaysFee(MASTER, target, self.nomin.priceToSpend(old_bal))
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'Transfer',
            fields={'from': MASTER, 'to': target, 'value': self.nomin.priceToSpend(old_bal)},
            location=self.nomin_proxy.address
        )

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Transfer',
            fields={
                'from': MASTER,
                'to': self.nomin_contract.address,
                'value': self.nomin.transferFeeIncurred(self.nomin.priceToSpend(old_bal))
            },
            location=self.nomin_proxy.address
        )

        self.assertEqual(self.nomin.balanceOf(target), self.nomin.priceToSpend(old_bal))
        self.assertLess(self.nomin.balanceOf(MASTER), 2)  # assert MASTER only has the tiniest bit of change

    def test_transferFromSenderPaysFee(self):
        target = fresh_account()

        self.nomin.giveNomins(MASTER, MASTER, 10 * UNIT)

        # Unauthorized transfers should not work
        self.assertReverts(self.nomin.transferFrom, DUMMY, MASTER, target, UNIT)

        # Neither should transfers that are too large for the allowance.
        self.nomin.approve(MASTER, DUMMY, UNIT)
        self.assertReverts(self.nomin.transferFrom, DUMMY, MASTER, target, 2 * UNIT)

        self.nomin.approve(MASTER, DUMMY, 10000 * UNIT)

        self.assertEqual(self.nomin.balanceOf(MASTER), 10 * UNIT)
        self.assertEqual(self.nomin.balanceOf(target), 0)

        # Should be impossible to transfer to the nomin contract itself.
        self.assertReverts(self.nomin.transferFrom, DUMMY, MASTER, self.nomin_contract.address, UNIT)

        self.nomin.transferFromSenderPaysFee(DUMMY, MASTER, target, 5 * UNIT)

        self.assertClose(self.nomin.balanceOf(MASTER), 5 * UNIT - self.nomin.transferFeeIncurred(5 * UNIT))
        self.assertEqual(self.nomin.balanceOf(target), 5 * UNIT)
        self.assertEqual(self.nomin.feePool(), self.nomin.transferFeeIncurred(5 * UNIT))

        self.nomin.debugFreezeAccount(MASTER, target)

        self.assertReverts(self.nomin.transferFrom, DUMMY, MASTER, target, UNIT)
        self.assertReverts(self.nomin.transferFrom, DUMMY, target, MASTER, UNIT)

        txr = self.nomin.unfreezeAccount(MASTER, target)

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'AccountUnfrozen',
            fields={'target': target},
            location=self.nomin_proxy.address
        )

        old_bal = self.nomin.balanceOf(MASTER)

        self.nomin.transferFromSenderPaysFee(DUMMY, MASTER, target, self.nomin.priceToSpend(old_bal))

        self.assertEqual(self.nomin.balanceOf(target), self.nomin.priceToSpend(old_bal))
        self.assertLess(self.nomin.balanceOf(MASTER), 3)  # assert MASTER only has the tiniest bit of change

    def test_confiscateBalance(self):
        target = W3.eth.accounts[2]

        self.assertEqual(self.nomin.court(), self.fake_court.contract.address)

        self.nomin.giveNomins(MASTER, target, 10 * UNIT)

        # The target must have some nomins.
        self.assertEqual(self.nomin.balanceOf(target), 10 * UNIT)

        motion_id = 1
        self.fake_court.setTargetMotionID(MASTER, target, motion_id)

        # Attempt to confiscate even though the conditions are not met.
        self.fake_court.setConfirming(MASTER, motion_id, False)
        self.fake_court.setVotePasses(MASTER, motion_id, False)
        self.assertReverts(self.fake_court.confiscateBalance, MASTER, target)

        self.fake_court.setConfirming(MASTER, motion_id, True)
        self.fake_court.setVotePasses(MASTER, motion_id, False)
        self.assertReverts(self.fake_court.confiscateBalance, MASTER, target)

        self.fake_court.setConfirming(MASTER, motion_id, False)
        self.fake_court.setVotePasses(MASTER, motion_id, True)
        self.assertReverts(self.fake_court.confiscateBalance, MASTER, target)

        # Set up the target balance to be confiscatable.
        self.fake_court.setConfirming(MASTER, motion_id, True)
        self.fake_court.setVotePasses(MASTER, motion_id, True)

        # Only the court should be able to confiscate balances.
        self.assertReverts(self.nomin.confiscateBalance, MASTER, target)

        # Actually confiscate the balance.
        pre_fee_pool = self.nomin.feePool()
        pre_balance = self.nomin.balanceOf(target)
        self.fake_court.confiscateBalance(MASTER, target)
        self.assertEqual(self.nomin.balanceOf(target), 0)
        self.assertEqual(self.nomin.feePool(), pre_fee_pool + pre_balance)
        self.assertTrue(self.nomin.frozen(target))

    def test_unfreezeAccount(self):
        target = fresh_account()

        # The nomin contract itself should not be unfreezable.
        tx_receipt = self.nomin.unfreezeAccount(MASTER, self.nomin_contract.address)
        self.assertTrue(self.nomin.frozen(self.nomin_contract.address))
        self.assertEqual(len(tx_receipt.logs), 0)

        # Unfreezing non-frozen accounts should not do anything.
        self.assertFalse(self.nomin.frozen(target))
        tx_receipt = self.nomin.unfreezeAccount(MASTER, target)
        self.assertFalse(self.nomin.frozen(target))
        self.assertEqual(len(tx_receipt.logs), 0)

        self.nomin.debugFreezeAccount(MASTER, target)
        self.assertTrue(self.nomin.frozen(target))

        # Only the owner should be able to unfreeze an account.
        self.assertReverts(self.nomin.unfreezeAccount, target, target)

        txr = self.nomin.unfreezeAccount(MASTER, target)

        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'AccountUnfrozen',
            fields={'target': target},
            location=self.nomin_proxy.address
        )

        self.assertFalse(self.nomin.frozen(target))

    def test_issue_burn(self):
        havven, acc1, acc2 = fresh_accounts(3)
        self.nomin.setHavven(MASTER, havven)

        # not even the owner can issue, only the havven contract
        self.assertReverts(self.nomin.issue, MASTER, acc1, 100 * UNIT)

        txr = self.nomin.publicIssue(havven, acc1, 100 * UNIT)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'Transfer',
            fields={'from': ZERO_ADDRESS, 'to': acc1, 'value': 100 * UNIT},
            location=self.nomin_proxy.address
        )
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Issued',
            fields={'account': acc1, 'amount': 100 * UNIT},
            location=self.nomin_proxy.address
        )

        self.assertEqual(self.nomin.balanceOf(acc1), 100 * UNIT)
        self.assertEqual(self.nomin.totalSupply(), 100 * UNIT)

        txr = self.nomin.publicIssue(havven, acc2, 200 * UNIT)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'Transfer',
            fields={'from': ZERO_ADDRESS, 'to': acc2, 'value': 200 * UNIT},
            location=self.nomin_proxy.address
        )
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Issued',
            fields={'account': acc2, 'amount': 200 * UNIT},
            location=self.nomin_proxy.address
        )

        self.assertEqual(self.nomin.balanceOf(acc2), 200 * UNIT)
        self.assertEqual(self.nomin.totalSupply(), 300 * UNIT)

        self.nomin.transfer(acc1, acc2, 50 * UNIT)
        self.assertNotEqual(self.nomin.totalSupply(), self.nomin.balanceOf(acc1) + self.nomin.balanceOf(acc2))
        self.assertEqual(self.nomin.totalSupply(), self.nomin.balanceOf(acc1) + self.nomin.balanceOf(acc2) + self.nomin.feePool())

        acc1_bal = self.nomin.balanceOf(acc1)
        # not even the owner can burn...
        self.assertReverts(self.nomin.burn, MASTER, acc1, acc1_bal)

        txr = self.nomin.publicBurn(havven, acc1, acc1_bal)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'Transfer',
            fields={'from': acc1, 'to': ZERO_ADDRESS, 'value': acc1_bal},
            location=self.nomin_proxy.address
        )
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Burned',
            fields={'account': acc1, 'amount': acc1_bal},
            location=self.nomin_proxy.address
        )

        self.assertEqual(self.nomin.totalSupply(), self.nomin.balanceOf(acc2) + self.nomin.feePool())

        # burning more than issued is allowed, as that logic is controlled in the havven contract
        self.nomin.publicBurn(havven, acc2, self.nomin.balanceOf(acc2))

        self.assertEqual(self.nomin.balanceOf(acc1), self.nomin.balanceOf(acc2), 0)

    def test_edge_issue_burn(self):
        havven, acc1, acc2 = fresh_accounts(3)
        self.nomin.setHavven(MASTER, havven)

        max_int = 2**256 - 1
        txr = self.nomin.publicIssue(havven, acc1, 100 * UNIT)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'Transfer',
            fields={'from': ZERO_ADDRESS, 'to': acc1, 'value': 100 * UNIT},
            location=self.nomin_proxy.address
        )
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Issued',
            fields={'account': acc1, 'amount': 100 * UNIT},
            location=self.nomin_proxy.address
        )
        self.assertReverts(self.nomin.publicIssue, havven, acc1, max_int)
        self.assertReverts(self.nomin.publicIssue, havven, acc2, max_int)
        # there shouldn't be a way to burn towards a larger value by overflowing
        self.assertReverts(self.nomin.publicBurn, havven, acc1, max_int)
        txr = self.nomin.publicBurn(havven, acc1, 100 * UNIT)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'Transfer',
            fields={'from': acc1, 'to': ZERO_ADDRESS, 'value': 100 * UNIT},
            location=self.nomin_proxy.address
        )
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Burned',
            fields={'account': acc1, 'amount': 100 * UNIT},
            location=self.nomin_proxy.address
        )

        # as long as no nomins exist, its a valid action
        txr = self.nomin.publicIssue(havven, acc2, max_int)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'Transfer',
            fields={'from': ZERO_ADDRESS, 'to': acc2, 'value': max_int},
            location=self.nomin_proxy.address
        )
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Issued',
            fields={'account': acc2, 'amount': max_int},
            location=self.nomin_proxy.address
        )

        txr = self.nomin.publicBurn(havven, acc2, max_int)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'Transfer',
            fields={'from': acc2, 'to': ZERO_ADDRESS, 'value': max_int},
            location=self.nomin_proxy.address
        )
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Burned',
            fields={'account': acc2, 'amount': max_int},
            location=self.nomin_proxy.address
        )

    def test_event_CourtUpdated(self):
        new_court = fresh_account()
        tx = self.nomin.setCourt(MASTER, new_court)
        self.assertEventEquals(self.nomin_event_dict,
                               tx.logs[0], "CourtUpdated",
                               {"newCourt": new_court},
                                self.nomin_proxy.address)

    def test_event_HavvenUpdated(self):
        new_havven = fresh_account()
        tx = self.nomin.setHavven(MASTER, new_havven)
        self.assertEventEquals(self.nomin_event_dict,
                               tx.logs[1], "HavvenUpdated",
                               {"newHavven": new_havven},
                                self.nomin_proxy.address)

    def test_event_AccountFrozen(self):
        target = fresh_account()
        self.nomin.clearNomins(MASTER, target)
        self.nomin.giveNomins(MASTER, target, 5 * UNIT)
        motion_id = 1
        self.fake_court.setTargetMotionID(MASTER, target, motion_id)
        self.fake_court.setConfirming(MASTER, motion_id, True)
        self.fake_court.setVotePasses(MASTER, motion_id, True)
        self.assertEqual(self.nomin.balanceOf(target), 5 * UNIT)
        txr = self.fake_court.confiscateBalance(MASTER, target)
        self.assertEqual(self.nomin.balanceOf(target), 0)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'AccountFrozen',
            fields={'target': target, 'balance': 5 * UNIT},
            location=self.nomin_proxy.address
        )

    def test_event_AccountUnfrozen(self):
        target = fresh_account()
        self.nomin.debugFreezeAccount(MASTER, target)
        txr = self.nomin.unfreezeAccount(MASTER, target)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[0], 'AccountUnfrozen',
            fields={'target': target},
            location=self.nomin_proxy.address
        )

    def test_event_Issued(self):
        issuer = fresh_account()
        self.nomin.setHavven(MASTER, MASTER)
        txr = self.nomin.publicIssue(MASTER, issuer, UNIT)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Issued',
            fields={'account': issuer,
                    'amount': UNIT},
            location=self.nomin_proxy.address
        )

    def test_event_Burned(self):
        burner = fresh_account()
        self.nomin.setHavven(MASTER, MASTER)
        self.nomin.publicIssue(MASTER, burner, UNIT)
        txr = self.nomin.publicBurn(MASTER, burner, UNIT)
        self.assertEventEquals(
            self.nomin_event_dict, txr.logs[1], 'Burned',
            fields={'account': burner,
                    'amount': UNIT},
            location=self.nomin_proxy.address
        )
        